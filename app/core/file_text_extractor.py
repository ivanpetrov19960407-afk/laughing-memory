from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image
from pytesseract import TesseractNotFoundError, image_to_string


@dataclass(frozen=True)
class ExtractedText:
    text: str
    metadata: dict[str, int]
    warnings: tuple[str, ...] = ()


class OCRNotAvailableError(RuntimeError):
    """Raised when OCR is requested but Tesseract is unavailable or disabled."""


class FileTextExtractor:
    def __init__(
        self,
        *,
        ocr_enabled: bool = True,
        tesseract_lang: str = "rus+eng",
        max_chars: int | None = None,
        max_pages: int | None = None,
    ) -> None:
        self._ocr_enabled = ocr_enabled
        self._tesseract_lang = tesseract_lang
        self._max_chars = max_chars
        self._max_pages = max_pages

    def extract(self, *, path: Path, file_type: str) -> ExtractedText:
        if file_type == "pdf":
            return self._extract_pdf(path)
        if file_type == "docx":
            return self._extract_docx(path)
        if file_type == "image":
            return self._extract_image(path)
        raise ValueError(f"Unsupported file type: {file_type}")

    def extract_from_bytes(
        self,
        data: bytes,
        ext: str,
        *,
        max_chars: int | None = None,
    ) -> ExtractedText:
        """Extract text from in-memory bytes. ext: 'pdf', 'docx', or 'image'."""
        file_type = ext.lower().strip()
        if file_type == "pdf":
            return self._extract_pdf_from_bytes(data, max_chars=max_chars)
        if file_type == "docx":
            return self._extract_docx_from_bytes(data, max_chars=max_chars)
        if file_type in ("image", "jpg", "jpeg", "png"):
            return self._extract_image_from_bytes(data)
        raise ValueError(f"Unsupported file type: {ext}")

    def _extract_pdf(self, path: Path) -> ExtractedText:
        try:
            from pypdf import PdfReader
        except ModuleNotFoundError as exc:
            raise ImportError("pypdf is required to extract text from PDF files.") from exc
        reader = PdfReader(str(path))
        pages_text: list[str] = []
        for page in reader.pages:
            pages_text.append(page.extract_text() or "")
        text = "\n".join(pages_text).strip()
        return ExtractedText(
            text=text,
            metadata={
                "pages": len(reader.pages),
                "characters": len(text),
            },
        )

    def _extract_docx(self, path: Path) -> ExtractedText:
        try:
            from docx import Document
        except ModuleNotFoundError as exc:
            raise ImportError("python-docx is required to extract text from DOCX files.") from exc
        document = Document(str(path))
        paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
        text = "\n".join(paragraphs).strip()
        return ExtractedText(
            text=text,
            metadata={
                "paragraphs": len(document.paragraphs),
                "characters": len(text),
            },
        )

    def _extract_image(self, path: Path) -> ExtractedText:
        if not self._ocr_enabled:
            raise OCRNotAvailableError("OCR disabled via configuration.")
        try:
            image = Image.open(path)
            text = image_to_string(image, lang=self._tesseract_lang) or ""
        except TesseractNotFoundError as exc:
            raise OCRNotAvailableError("Tesseract OCR is not available.") from exc
        text = text.strip()
        return ExtractedText(text=text, metadata={"characters": len(text)})

    def _extract_pdf_from_bytes(
        self,
        data: bytes,
        *,
        max_chars: int | None = None,
    ) -> ExtractedText:
        try:
            from pypdf import PdfReader
        except ModuleNotFoundError as exc:
            raise ImportError("pypdf is required to extract text from PDF files.") from exc
        reader = PdfReader(BytesIO(data))
        limit_pages = self._max_pages
        pages_text: list[str] = []
        for i, page in enumerate(reader.pages):
            if limit_pages is not None and i >= limit_pages:
                break
            pages_text.append(page.extract_text() or "")
        text = "\n".join(pages_text).strip()
        warnings_list: list[str] = []
        if limit_pages is not None and len(reader.pages) > limit_pages:
            warnings_list.append("pages_truncated")
        cap = max_chars if max_chars is not None else self._max_chars
        if cap is not None and len(text) > cap:
            text = text[:cap].rsplit("\n", 1)[0].strip() or text[:cap]
            warnings_list.append("chars_truncated")
        return ExtractedText(
            text=text,
            metadata={"pages": len(pages_text), "characters": len(text)},
            warnings=tuple(warnings_list),
        )

    def _extract_docx_from_bytes(
        self,
        data: bytes,
        *,
        max_chars: int | None = None,
    ) -> ExtractedText:
        try:
            from docx import Document
        except ModuleNotFoundError as exc:
            raise ImportError("python-docx is required to extract text from DOCX files.") from exc
        document = Document(BytesIO(data))
        paragraphs = [p.text for p in document.paragraphs if p.text]
        text = "\n".join(paragraphs).strip()
        warnings_list: list[str] = []
        cap = max_chars if max_chars is not None else self._max_chars
        if cap is not None and len(text) > cap:
            text = text[:cap].rsplit("\n", 1)[0].strip() or text[:cap]
            warnings_list.append("chars_truncated")
        return ExtractedText(
            text=text,
            metadata={"paragraphs": len(document.paragraphs), "characters": len(text)},
            warnings=tuple(warnings_list),
        )

    def _extract_image_from_bytes(self, data: bytes) -> ExtractedText:
        if not self._ocr_enabled:
            raise OCRNotAvailableError("OCR disabled via configuration.")
        try:
            image = Image.open(BytesIO(data))
            text = image_to_string(image, lang=self._tesseract_lang) or ""
        except TesseractNotFoundError as exc:
            raise OCRNotAvailableError("Tesseract OCR is not available.") from exc
        text = text.strip()
        return ExtractedText(text=text, metadata={"characters": len(text)}, warnings=())
