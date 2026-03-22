"""Extract text from PDF files using PyMuPDF, with Gemini Vision fallback for scanned PDFs."""

from pathlib import Path

from core.logger import get_logger

log = get_logger(__name__)


def extract_text(path: str | Path) -> str:
    """Extract all text from a PDF file.

    Tries PyMuPDF first. If the extracted text is too short (likely a scanned PDF),
    falls back to Gemini Vision via llm_router.

    Args:
        path: Absolute or relative path to the PDF file.

    Returns:
        Extracted text string, or empty string on failure.
    """
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        pages_text = [page.get_text() for page in doc]
        doc.close()
        full_text = "\n".join(pages_text).strip()
        if len(full_text) > 200:
            log.info("PDF extracted via PyMuPDF: %d chars", len(full_text))
            return full_text
        log.warning("PyMuPDF returned short text (%d chars), trying Vision fallback", len(full_text))
        return _vision_fallback(path)
    except ImportError:
        log.error("PyMuPDF (fitz) not installed. Run: pip install pymupdf")
        return ""
    except Exception as e:
        log.error("PDF extraction failed for %s: %s", path, e)
        return _vision_fallback(path)


def _vision_fallback(path: str | Path) -> str:
    """Use Gemini Vision to read a scanned PDF by converting pages to images."""
    try:
        import fitz
        import base64
        from core.llm_router import call

        doc = fitz.open(str(path))
        texts = []
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            b64 = base64.b64encode(img_bytes).decode()
            prompt = (
                f"This is a scanned resume page. Extract ALL text exactly as written. "
                f"Return only the raw text, no commentary.\n"
                f"[IMAGE: data:image/png;base64,{b64[:500]}...]"
            )
            text = call(prompt, task_type="resume_parsing", max_tokens=2048)
            texts.append(text)
        doc.close()
        result = "\n".join(texts)
        log.info("Vision fallback extracted %d chars", len(result))
        return result
    except Exception as e:
        log.error("Vision fallback failed: %s", e)
        return ""
