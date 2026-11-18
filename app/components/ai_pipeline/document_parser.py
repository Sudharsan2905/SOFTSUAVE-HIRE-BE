"""Extract plain text from PDF and DOCX resumes."""

import io

ALLOWED_RESUME_EXT = (".pdf", ".docx")
ALLOWED_PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp")


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _docx_text(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    # hyperlinks such as mailto: live in relationships, not paragraph text
    parts.extend(rel.target_ref for rel in doc.part.rels.values() if rel.is_external)
    return "\n".join(p for p in parts if p)


def extract_text(filename: str, data: bytes) -> str:
    """Return the text of a resume; raises ValueError for unsupported/corrupt files."""
    name = filename.lower()
    try:
        if name.endswith(".pdf"):
            return _pdf_text(data).strip()
        if name.endswith(".docx"):
            return _docx_text(data).strip()
    except Exception as exc:
        raise ValueError(f"Could not read {filename}: {type(exc).__name__}: {exc}") from exc
    raise ValueError(f"Unsupported resume type: {filename} (use PDF or DOCX)")
