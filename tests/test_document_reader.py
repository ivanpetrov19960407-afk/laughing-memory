from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from pytesseract import TesseractNotFoundError, get_tesseract_version

from app.bot import handlers
from app.core.file_text_extractor import FileTextExtractor, OCRNotAvailableError
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
        )
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
