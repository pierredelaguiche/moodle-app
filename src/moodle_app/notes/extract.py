"""Text extraction for course files, with page markers so generated notes can cite pages."""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md", ".py", ".c", ".h", ".java", ".sql", ".sh", ".tex"}
SOURCE_SUFFIXES = {".pdf"} | TEXT_SUFFIXES
PAGE_MARK = "<!-- p.{} -->"
_PAGE_RE = re.compile(r"(?m)^<!-- p\.\d+(?::[^>]*)? -->$")


class PdfSupportMissing(RuntimeError):
    """pymupdf is not installed."""


def _open_pdf(path):
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:
            raise PdfSupportMissing("Reading PDFs needs pymupdf:  python3 -m pip install pymupdf")
    return fitz.open(str(path))


def clean_pages(pages):
    """Drop running headers/footers (lines on >= 60% of pages, only when there are 4+ pages) and bare page numbers."""
    lines_per_page = [[ln.strip() for ln in p.splitlines() if ln.strip()] for p in pages]
    repeated = set()
    if len(pages) >= 4:
        c = Counter(ln for lines in lines_per_page for ln in set(lines))
        repeated = {ln for ln, n in c.items() if n >= 0.6 * len(pages) and len(ln) < 80}
    out = []
    for lines in lines_per_page:
        out.append("\n".join(ln for ln in lines if ln not in repeated and not re.fullmatch(r"\d{1,3}", ln)))
    return out


_cache_dir = None


def configure_cache(directory):
    """The one place extracted PDF text is kept (outside your study folder). Every extraction goes through `extract`,
    so a PDF is read once per (path, size, modified time), whoever asks: notes, 'copy text', or 'read'."""
    global _cache_dir
    _cache_dir = Path(directory) if directory else None


def _cache_file(path):
    try:
        st = path.stat()
    except OSError:
        return None
    key = hashlib.sha256(f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:24]
    return _cache_dir / f"{key}.txt"


def extract(path):
    """The file's text (see `extract_info`)."""
    return extract_info(path)[0]


def extract_info(path):
    """(text, from_cache). PDFs get one `<!-- p.N -->` marker per page (`p.N: no text` for image-only pages)."""
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        return path.read_text(encoding="utf-8", errors="replace"), False
    cf = _cache_file(path) if _cache_dir else None
    if cf is not None and cf.is_file():
        try:
            return cf.read_text(encoding="utf-8"), True
        except OSError:
            pass
    text = _extract_pdf(path)
    if cf is not None:
        try:
            cf.parent.mkdir(parents=True, exist_ok=True)
            cf.write_text(text, encoding="utf-8")
        except OSError:
            pass
    return text, False


def _extract_pdf(path):
    doc = _open_pdf(path)
    try:
        pages = [str(pg.get_text() or "") for pg in doc]
    finally:
        doc.close()
    out = []
    for i, txt in enumerate(clean_pages(pages), 1):
        out.append(PAGE_MARK.format(i) if txt.strip() else f"<!-- p.{i}: no text (image only) -->")
        if txt.strip():
            out.append(txt)
    return "\n".join(out)


def has_text(text):
    """False when a document is only page markers (e.g. a scanned PDF)."""
    return bool(_PAGE_RE.sub("", text).strip())


def split_text(text, max_chars):
    """Split into chunks of at most max_chars, preferring page boundaries, then line boundaries."""
    if len(text) <= max_chars:
        return [text]
    starts = [m.start() for m in _PAGE_RE.finditer(text)] or [0]
    if starts[0] != 0:
        starts.insert(0, 0)
    blocks = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
    chunks, cur = [], ""
    for b in blocks:
        while len(b) > max_chars:                       # a single page longer than the limit: cut on lines
            cut = b.rfind("\n", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(b[:cut])
            b = b[cut:]
        if len(cur) + len(b) > max_chars and cur:
            chunks.append(cur)
            cur = ""
        cur += b
    if cur.strip():
        chunks.append(cur)
    return chunks
