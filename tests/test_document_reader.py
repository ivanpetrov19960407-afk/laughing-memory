from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from pytesseract import TesseractNotFoundError, get_tesseract_version

from app.bot import handlers
from app.core.document_qa import select_relevant_chunks, select_relevant_chunks_with_scores, split_text
from app.core.file_text_extractor import ExtractedText, FileTextExtractor, OCRNotAvailableError
from app.infra.document_session_store import DocumentSessionStore


def _build_simple_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        (
            "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj"
        ),
        (
            f"4 0 obj << /Length {len(f'BT /F1 24 Tf 72 120 Td ({escaped}) Tj ET')} >> stream\n"
            f"BT /F1 24 Tf 72 120 Td ({escaped}) Tj ET\nendstream endobj"
        ),
        "5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf += obj.encode("latin1") + b"\n"
    xref_offset = len(pdf)
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        xref += f"{offset:010d} 00000 n \n"
    trailer = (
        f"trailer << /Root 1 0 R /Size {len(objects) + 1} >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    )
    pdf += xref.encode("latin1") + trailer.encode("latin1")
    return pdf


def _tesseract_available() -> bool:
    try:
        get_tesseract_version()
    except TesseractNotFoundError:
        return False
    return True


def test_extract_pdf_text(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(_build_simple_pdf("Hello PDF"))
    extractor = FileTextExtractor()

    extracted = extractor.extract(path=pdf_path, file_type="pdf")

    assert "Hello PDF" in extracted.text


def test_extract_docx_text(tmp_path: Path) -> None:
    docx_module = pytest.importorskip("docx")
    doc_path = tmp_path / "sample.docx"
    document = docx_module.Document()
    document.add_paragraph("Hello DOCX")
    document.save(doc_path)
    extractor = FileTextExtractor()

    extracted = extractor.extract(path=doc_path, file_type="docx")

    assert "Hello DOCX" in extracted.text


def test_extract_image_text(tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    image = Image.new("RGB", (400, 120), color="white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.text((10, 40), "Hello OCR", fill="black", font=font)
    image.save(image_path)
    extractor = FileTextExtractor(ocr_enabled=True)

    if not _tesseract_available():
        with pytest.raises(OCRNotAvailableError):
            extractor.extract(path=image_path, file_type="image")
        return

    extracted = extractor.extract(path=image_path, file_type="image")

    assert "Hello" in extracted.text


def test_extract_image_ocr_disabled_raises(tmp_path: Path) -> None:
    """When OCR is disabled, image extraction raises OCRNotAvailableError (no tesseract needed)."""
    image_path = tmp_path / "sample.png"
    image = Image.new("RGB", (100, 100), color="white")
    image.save(image_path)
    extractor = FileTextExtractor(ocr_enabled=False)

    with pytest.raises(OCRNotAvailableError):
        extractor.extract(path=image_path, file_type="image")


class DummyLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._index = 0

    async def generate_text(self, *, model: str, messages: list[dict[str, object]]):  # type: ignore[override]
        response = self._responses[self._index]
        self._index += 1
        return response


class DummyOrchestrator:
    def __init__(self, facts_only: bool = True) -> None:
        self._facts_only = facts_only

    def is_facts_only(self, user_id: int) -> bool:
        return self._facts_only


@pytest.mark.anyio
async def test_document_flow_summary_and_qa(tmp_path: Path) -> None:
    docx_module = pytest.importorskip("docx")
    doc_path = tmp_path / "integration.docx"
    document = docx_module.Document()
    document.add_paragraph("The document says the sky is blue.")
    document.save(doc_path)
    extractor = FileTextExtractor()
    extracted = extractor.extract(path=doc_path, file_type="docx")
    text_path = tmp_path / "text.txt"
    text_path.write_text(extracted.text, encoding="utf-8")
    store_path = tmp_path / "sessions.json"
    store = DocumentSessionStore(store_path)
    session = store.create_session(
        user_id=1,
        chat_id=1,
        file_path=str(doc_path),
        file_type="docx",
        text_path=str(text_path),
    )
    llm = DummyLLM(["Summary output", "Answer output"])
    settings = SimpleNamespace(openai_model="gpt-3.5-turbo", perplexity_model="sonar")
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "document_store": store,
                "llm_client": llm,
                "settings": settings,
                "orchestrator": DummyOrchestrator(),
            }
        ),
        chat_data={},
    )

    summary_result = await handlers._handle_document_summary(
        context,
        user_id=1,
        chat_id=1,
        doc_id=session.doc_id,
    )

    assert summary_result.text == "Summary output"
    assert any(action.id == "document.qa" for action in summary_result.actions)

    store.set_state(doc_id=session.doc_id, state="qa_mode")
    qa_result = await handlers._handle_document_question(
        context,
        user_id=1,
        chat_id=1,
        question="What color is the sky?",
    )

    assert qa_result.text == "Answer output"
    assert any(action.id == "document.qa_exit" for action in qa_result.actions)


def test_select_relevant_chunks_no_match_returns_empty() -> None:
    text = "The quick brown fox jumps. Weather is sunny."
    chunks = select_relevant_chunks(text, "nonexistentwordxyz", top_k=2)
    assert chunks == []


def test_select_relevant_chunks_match_returns_chunks() -> None:
    text = "The contract states that payment is due in 30 days. Late fee applies."
    chunks = select_relevant_chunks(text, "payment 30 days", top_k=2)
    assert len(chunks) >= 1
    combined = " ".join(chunks).lower()
    assert "payment" in combined or "30" in combined


def test_split_text_chunk_size_overlap() -> None:
    text = "a" * 2000
    chunks = split_text(text, chunk_size=500, overlap=100)
    assert len(chunks) >= 2
    assert sum(len(c) for c in chunks) >= len(text) - 500


def test_extract_pdf_from_bytes() -> None:
    pytest.importorskip("pypdf")
    pdf_bytes = _build_simple_pdf("Bytes PDF")
    extractor = FileTextExtractor()
    extracted = extractor.extract_from_bytes(pdf_bytes, "pdf")
    assert isinstance(extracted, ExtractedText)
    assert "Bytes PDF" in extracted.text
    assert extracted.metadata.get("pages") == 1
    assert extracted.metadata.get("characters", 0) > 0


def test_extracted_text_has_warnings_field() -> None:
    from io import BytesIO
    docx_module = pytest.importorskip("docx")
    doc = docx_module.Document()
    doc.add_paragraph("Short.")
    buf = BytesIO()
    doc.save(buf)
    extractor = FileTextExtractor(max_chars=50, max_pages=10)
    extracted = extractor.extract_from_bytes(buf.getvalue(), "docx", max_chars=50)
    assert hasattr(extracted, "warnings")
    assert isinstance(extracted.warnings, tuple)


def test_document_session_store_ttl_expires(tmp_path: Path) -> None:
    from datetime import datetime, timezone, timedelta
    store_path = tmp_path / "sessions.json"
    now = datetime.now(timezone.utc)
    # Store uses max(60, ttl_seconds), so effective TTL is 60s
    store = DocumentSessionStore(store_path, ttl_seconds=60, now_provider=lambda: now)
    session = store.create_session(
        user_id=1,
        chat_id=1,
        file_path=str(tmp_path / "f"),
        file_type="pdf",
        text_path=str(tmp_path / "t.txt"),
    )
    assert session.expires_at > now
    # Simulate time passing: session expires (after 61s)
    def later() -> datetime:
        return now + timedelta(seconds=61)
    store_later = DocumentSessionStore(store_path, ttl_seconds=60, now_provider=later)
    store_later.load()
    active, status = store_later.get_active_with_status(user_id=1, chat_id=1)
    assert active is None
    assert status == "expired"


def test_document_session_store_close_clears_active(tmp_path: Path) -> None:
    store_path = tmp_path / "sessions.json"
    store = DocumentSessionStore(store_path, ttl_seconds=7200)
    session = store.create_session(
        user_id=2,
        chat_id=2,
        file_path=str(tmp_path / "f"),
        file_type="docx",
        text_path=str(tmp_path / "t.txt"),
    )
    assert store.get_active(user_id=2, chat_id=2) is not None
    closed = store.close_active(user_id=2, chat_id=2)
    assert closed is not None
    assert store.get_active(user_id=2, chat_id=2) is None
