from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from pytesseract import TesseractNotFoundError, image_to_string


@dataclass(frozen=True)
class ExtractedText:
    text: str
    metadata: dict[str, int]


class OCRNotAvailableError(RuntimeError):
    """Raised when OCR is requested but Tesseract is unavailable or disabled."""


class FileTextExtractor:
    def __init__(
        self,
        *,
        ocr_enabled: bool = True,
        tesseract_lang: str = "rus+eng",
    ) -> None:
        self._ocr_enabled = ocr_enabled
        self._tesseract_lang = tesseract_lang

    def extract(self, *, path: Path, file_type: str) -> ExtractedText:
        if file_type == "pdf":
            return self._extract_pdf(path)
        if file_type == "docx":
            return self._extract_docx(path)
        if file_type == "image":
            return self._extract_image(path)
        raise ValueError(f"Unsupported file type: {file_type}")

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
