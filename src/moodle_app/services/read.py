"""Read: PDFs -> text files, kept per course in '<course>/.formatted txt/' (a hidden folder; the AI page lists it).

Default (the whole course folder): one text file per first-level sub-folder (Lectures.txt, Workshops.txt, ...; PDFs directly in
the course -> misc.txt). Any other folder works too (`read FOLDER`, or the AI page): its text goes in ONE file named after the
folder's path inside the same course, so everything about a course stays in that course's hidden folder. A folder outside
every course keeps its own '.formatted txt/'. The old visible 'formatted txt/' is moved in on first use. Page markers and
repeated headers/footers are handled by notes.extract, which also remembers what it already extracted."""
from __future__ import annotations

import shutil
from pathlib import Path

from ..notes.extract import PdfSupportMissing, extract, extract_info

FORMATTED = ".formatted txt"
LEGACY = "formatted txt"


def _skip(part):
    return part in (FORMATTED, LEGACY, "_old_versions") or part.startswith(".")


def _pdfs(sub: Path):
    return sorted(p for p in sub.rglob("*.pdf") if not any(_skip(part) for part in p.relative_to(sub).parts[:-1]))


def course_of(folder: Path, root: "Path | None"):
    """(course folder, path of `folder` inside it) when `folder` is in a course under `root`, else (None, None)."""
    if root is None:
        return None, None
    try:
        parts = Path(folder).resolve().relative_to(Path(root).resolve()).parts
    except ValueError:
        return None, None
    return (Path(root).resolve() / parts[0], Path(*parts[1:])) if parts else (None, None)


def out_dir_for(folder: Path, root: "Path | None" = None) -> Path:
    course, _ = course_of(folder, root)
    return (course or Path(folder)) / FORMATTED


def adopt_legacy(base: Path, log=print):
    """Move an old visible 'formatted txt' folder next to it into the hidden one (nothing is overwritten)."""
    old, new = base / LEGACY, base / FORMATTED
    if not old.is_dir() or old.is_symlink():
        return
    new.mkdir(exist_ok=True)
    for f in old.iterdir():
        if not (new / f.name).exists():
            shutil.move(str(f), str(new / f.name))
    try:
        old.rmdir()
        log(f"  moved '{LEGACY}' to '{FORMATTED}'")
    except OSError:
        pass


def _bundle(label, pdfs, out_dir, log):
    sections = []
    for pdf in pdfs:
        log(f"  [{label}] {pdf.name}")
        try:
            text, cached = extract_info(pdf)
            if cached:
                log("    (already extracted: reused)")
        except PdfSupportMissing:
            raise
        except Exception as e:
            text = f"[ERROR extracting {pdf.name}: {e}]"
        sections.append(f"{'=' * 80}\nFILE: {pdf.name}\nPATH: {pdf.parent}\n{'=' * 80}\n{text}")
    name = label.replace("/", "__").replace(" ", "_")
    out = out_dir / f"{name}.txt"
    out.write_text(f"{'#' * 80}\n# {label.upper()}\n{'#' * 80}\n\n" + "\n\n".join(sections), encoding="utf-8")
    log(f"  -> {out.name}  ({len(sections)} file{'s' if len(sections) != 1 else ''})")
    return len(sections)


def read_folder(folder: Path, log=print, root: "Path | None" = None) -> int:
    """Returns the number of PDFs converted. Raises RuntimeError when pymupdf is missing."""
    folder = Path(folder)
    course, inside = course_of(folder, root)
    base = course or folder
    out_dir = base / FORMATTED
    adopt_legacy(base, log)
    if course is not None and inside != Path("."):               # a folder inside a course: one file, named by its path
        pdfs = _pdfs(folder)
        if not pdfs:
            log("No PDFs found.")
            return 0
        out_dir.mkdir(exist_ok=True)
        total = _bundle(inside.as_posix(), pdfs, out_dir, log)
    else:
        cats = {}
        root_pdfs = sorted(folder.glob("*.pdf"))
        if root_pdfs:
            cats["misc"] = root_pdfs
        for d in sorted(x for x in folder.iterdir() if x.is_dir() and not _skip(x.name) and not x.name.startswith("_")):
            pdfs = _pdfs(d)
            if pdfs:
                cats[d.name] = pdfs
        if not cats:
            log("No PDFs found.")
            return 0
        out_dir.mkdir(exist_ok=True)
        total = sum(_bundle(label, pdfs, out_dir, log) for label, pdfs in cats.items())
    log(f"Done. {total} PDF(s) in '{out_dir}'")
    return total


TEXT_CAP = 3_000_000                     # characters returned to the page in one go
TMP_KEEP = 24 * 3600                     # temporary .txt files are removed after a day


def pdf_text(files, tmp_dir, now=None):
    """Plain text of one or more PDFs for pasting into an AI chat, using the same extractor as `read`.

    `files` is a list of (label, Path). The text is also written to '<tmp_dir>/<name>.txt' (a temporary file, outside your
    study folder, removed after a day). Returns {text, files:[{label, chars, error}], chars, words, tokens, tmp, truncated}.
    Raises RuntimeError when pymupdf is missing."""
    import time
    tmp_dir = Path(tmp_dir)
    now = now or time.time()
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for old in tmp_dir.glob("*.txt"):                       # tidy up earlier temporary files
        try:
            if now - old.stat().st_mtime > TMP_KEEP:
                old.unlink()
        except OSError:
            pass
    parts, info = [], []
    for label, path in files:
        try:
            text, cached = extract_info(path)
            err = None
        except PdfSupportMissing:
            raise
        except Exception as e:
            text, err, cached = "", f"could not read: {e}", False
        info.append({"label": label, "chars": len(text), "error": err, "cached": cached})
        parts.append((label, text, err))
    if len(parts) == 1:
        body = parts[0][1] if not parts[0][2] else f"[{parts[0][2]}]"
    else:
        body = "\n\n".join(f"{'=' * 60}\nFILE: {label}\n{'=' * 60}\n{text if not err else '[' + err + ']'}" for label, text, err in parts)
    truncated = len(body) > TEXT_CAP
    shown = body[:TEXT_CAP]
    name = (Path(files[0][0]).stem if len(files) == 1 else f"{len(files)} PDFs") if files else "text"
    name = "".join(c if c.isalnum() or c in " ._-" else "_" for c in name).strip() or "text"
    out = tmp_dir / f"{name}.txt"
    out.write_text(body, encoding="utf-8")
    return {"text": shown, "files": info, "chars": len(body), "words": len(body.split()), "tokens": len(body) // 4,
            "tmp": str(out), "truncated": truncated}
