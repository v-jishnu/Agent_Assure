"""
Document loader for AgentAssure policy ingestion.

Supports PDF, DOCX, TXT, and Markdown.  Returns plain text
suitable for feeding into the policy extractor.

All heavy dependencies (pypdf, python-docx) are optional — the loader
raises a clear ImportError with install instructions if they are missing.
"""

from __future__ import annotations

from pathlib import Path


def load_document(path: str) -> str:
    """
    Load a document and return its text content.

    Args:
        path: Path to the file.  Supported extensions: .pdf, .docx, .txt, .md

    Returns:
        Extracted plain text.

    Raises:
        ValueError:   Unsupported file extension.
        ImportError:  Required optional dependency not installed.
        FileNotFoundError: File does not exist.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")

    ext = p.suffix.lower()

    if ext == ".pdf":
        return _load_pdf(p)
    elif ext == ".docx":
        return _load_docx(p)
    elif ext in (".txt", ".md", ".markdown", ".rst"):
        return p.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError(
            f"Unsupported file type '{ext}'. "
            "Supported types: .pdf, .docx, .txt, .md"
        )


def _load_pdf(path: Path) -> str:
    try:
        import pypdf
    except ImportError:
        raise ImportError(
            "pypdf is required to load PDF files.\n"
            "Install it with: pip install 'agentassure[ingest]'\n"
            "or:              pip install pypdf"
        )

    text_parts = []
    with open(path, "rb") as f:
        reader = pypdf.PdfReader(f)
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)

    return "\n".join(text_parts)


def _load_docx(path: Path) -> str:
    try:
        import docx
    except ImportError:
        raise ImportError(
            "python-docx is required to load DOCX files.\n"
            "Install it with: pip install 'agentassure[ingest]'\n"
            "or:              pip install python-docx"
        )

    doc   = docx.Document(str(path))
    lines = [para.text for para in doc.paragraphs if para.text.strip()]
    return "\n".join(lines)
