"""`create notes`: manual, token-guarded generation of study material from a module's course files.

Nothing here runs automatically. You call it for a module, it prints a plan (what will be sent, to which endpoint, with a
token estimate), asks for confirmation when the job is big, and only then calls the LLM.

Per module it can produce, under <module>/Notes/:
    Lectures/<lecture>.md                lecture notes                       (what: lectures)
    Workshops/<workshop> - summary.md    workshop summary                    (what: summaries)
    Workshops/<workshop>.py              fill-in file, questions as comments (what: code)
    Study guide.md                       whole-course guide, built from the notes above (what: guide)

Safety rules: a file you edited, or one the tool did not write, is never overwritten (unless --force, which keeps a .bak);
an unchanged job is skipped for free; every write is atomic and recorded in the database (table notes_manifest).
"""
import hashlib
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import prompts
from .extract import SOURCE_SUFFIXES, extract, has_text, split_text
from ..llm.providers import LLMError, LLMLimit, estimate_tokens

WEEK_RE = re.compile(r"week[\s_\-]*0*(\d+)", re.I)
PROMPT_OVERHEAD = 1200          # tokens reserved for system prompt + instructions around the course text
DEFAULTS = {
    "output_dir": "Notes",
    "lecture_dirs": ["Lectures"],
    "workshop_dirs": ["Workshops"],
    "coursework_dirs": ["Coursework"],
    "confirm_tokens": 30000,      # ask before sending more than this many input tokens in one run (the guide always asks)
    "max_input_tokens": 100000,   # one request never carries more than this; longer sources are split
    "prompts_dir": None,
    "max_output": {"lecture_notes": 4000, "workshop_summary": 3000, "workshop_code": 4000, "coursework_notes": 3000, "study_guide": 8000},
}
KIND_ORDER = ["lecture_notes", "workshop_summary", "workshop_code", "coursework_notes", "study_guide"]
WHAT = {
    "lectures": {"lecture_notes"},
    "summaries": {"workshop_summary"},
    "code": {"workshop_code"},
    "workshops": {"workshop_summary", "workshop_code"},
    "coursework": {"coursework_notes"},
    "guide": {"study_guide"},
    "course": {"study_guide"},
    "all": set(KIND_ORDER),
}


def notes_config(cfg):
    out = {**DEFAULTS, **(cfg or {})}
    out["max_output"] = {**DEFAULTS["max_output"], **(cfg or {}).get("max_output", {})}
    return out


def parse_what(text):
    kinds = set()
    for w in (text or "all").replace(" ", "").split(","):
        if w not in WHAT:
            raise ValueError(f"unknown part '{w}'. Choose from: {', '.join(WHAT)}")
        kinds |= WHAT[w]
    return kinds


def week_of(path, module):
    try:
        rel = "/".join(Path(path).relative_to(module).parts)
    except ValueError:
        rel = str(path)
    m = WEEK_RE.search(rel)
    return int(m.group(1)) if m else None


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_hash(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def slug(stem):
    return re.sub(r"[^\w\-]+", "_", stem).strip("_") or "workshop"


@dataclass
class Job:
    kind: str
    title: str
    week: "int | None"
    source: "Path | None"
    out: Path
    version: str = ""
    status: str = "new"            # new | refresh | overwrite | fresh | kept
    texts: list = field(default_factory=list)
    in_tokens: int = 0
    out_tokens: int = 0
    note: str = ""
    input_hash: str = ""

    @property
    def runs(self):
        return self.status in ("new", "refresh", "overwrite")


@dataclass
class Plan:
    module: Path
    out_root: Path
    jobs: list
    cfg: dict
    manifest: dict

    def runnable(self):
        return [j for j in self.jobs if j.runs]

    @property
    def in_tokens(self):
        return sum(j.in_tokens for j in self.runnable())

    @property
    def out_tokens(self):
        return sum(j.out_tokens for j in self.runnable())

    def needs_confirm(self):
        return any(j.kind == "study_guide" for j in self.runnable()) or self.in_tokens > self.cfg["confirm_tokens"]


# ---------- discovery ----------
def find_sources(module, dir_names, out_name):
    wanted = {d.lower() for d in dir_names}
    found = []
    for d in sorted(p for p in module.iterdir() if p.is_dir() and p.name.lower() in wanted):
        for f in d.rglob("*"):
            parts = f.relative_to(module).parts
            if (f.is_file() and f.suffix.lower() in SOURCE_SUFFIXES and not f.name.startswith(".")
                    and not any(x.startswith((".", "_")) or x in ("formatted txt", out_name) for x in parts[:-1])):
                found.append(f)
    return sorted(found, key=lambda f: (week_of(f, module) or 0, str(f).lower()))


def _unique(out, taken):
    base, n = out, 1
    while str(out).lower() in taken:
        n += 1
        out = base.with_name(f"{base.stem}-{n}{base.suffix}")
    taken.add(str(out).lower())
    return out


def _decide(job, input_hash, manifest, rel, force):
    if not job.out.exists():
        return "new"
    e = manifest.get(rel)
    if not e or e.get("content_hash") != file_hash(job.out):     # not written by the tool, or edited since
        return "overwrite" if force else "kept"
    if force or e.get("input_hash") != input_hash or e.get("version") != job.version:
        return "refresh"
    return "fresh"


def _split_for(text, max_in):
    return split_text(text, max(4000, (max_in - PROMPT_OVERHEAD) * 4))


def job_key(kind, source_rel):
    return f"{kind}|{source_rel}"


def build_plan(module, what, week, cfg, manifest=None, force=False, log=print, only=None, select=None):
    """Work out what would be generated and how many tokens it would take. Calls no LLM."""
    nc = notes_config(cfg)
    module = Path(module)
    out_root = module / nc["output_dir"]
    manifest = manifest if manifest is not None else {}
    jobs, taken = [], set()
    text_cache = {}

    def text_of(src):
        if src not in text_cache:
            text_cache[src] = extract(src)
        return text_cache[src]

    sources = []
    if only is not None:                       # exactly these files (picked in the app), wherever they sit in the module
        wdirs = {d.lower() for d in nc["workshop_dirs"]}
        cdirs = {d.lower() for d in nc["coursework_dirs"]}
        for f in sorted(only, key=lambda f: str(f).lower()):
            if f.suffix.lower() not in SOURCE_SUFFIXES or f.name.startswith("."):
                continue
            if any(x.lower() in cdirs for x in f.relative_to(module).parts[:-1]):
                if "coursework_notes" in what:
                    sources.append(("coursework_notes", f))
            elif any(x.lower() in wdirs for x in f.relative_to(module).parts[:-1]):
                sources += [(k, f) for k in ("workshop_summary", "workshop_code") if k in what]
            elif "lecture_notes" in what:
                sources.append(("lecture_notes", f))
    elif what & {"lecture_notes"}:
        sources += [("lecture_notes", f) for f in find_sources(module, nc["lecture_dirs"], nc["output_dir"])]
    if only is None and "coursework_notes" in what:
        sources += [("coursework_notes", f) for f in find_sources(module, nc["coursework_dirs"], nc["output_dir"])]
    if only is None and what & {"workshop_summary", "workshop_code"}:
        ws = find_sources(module, nc["workshop_dirs"], nc["output_dir"])
        sources += [(k, f) for f in ws for k in ("workshop_summary", "workshop_code") if k in what]
    if select is not None:                         # an edited plan: exactly these jobs (kind|source path inside the module)
        sources = [(k, f) for k, f in sources if job_key(k, f.relative_to(module).as_posix()) in select]
    for kind, f in sources:
        wk = week_of(f, module)
        if week is not None and wk != week:
            continue
        if kind == "lecture_notes":
            out = out_root / "Lectures" / f"{f.stem}.md"
        elif kind == "coursework_notes":
            out = out_root / "Coursework" / f"{f.stem}.md"
        elif kind == "workshop_summary":
            out = out_root / "Workshops" / f"{f.stem} - summary.md"
        else:
            out = out_root / "Workshops" / f"{slug(f.stem)}.py"
        out = _unique(out, taken)
        version, _ = prompts.get(kind, nc["prompts_dir"])
        job = Job(kind, f.stem, wk, f, out, version)
        try:
            text = text_of(f)
        except Exception as e:
            job.status, job.note = "kept", f"cannot read: {e}"
            jobs.append(job)
            continue
        job.texts = _split_for(text, nc["max_input_tokens"])
        rel = out.relative_to(module.parent).as_posix()
        job.input_hash = sha(version + "\0" + text)
        job.status = _decide(job, job.input_hash, manifest, rel, force)
        if job.status == "kept":
            job.note = "edited by you or not written by the tool (use --force to replace)"
        if not has_text(text):
            job.status, job.note = "kept", "no text found (scanned/image-only file?)"
        if job.runs:
            job.in_tokens = sum(estimate_tokens(t) + PROMPT_OVERHEAD for t in job.texts)
            job.out_tokens = len(job.texts) * nc["max_output"][kind]
        if len(job.texts) > 1:
            job.note = f"split in {len(job.texts)} parts"
        jobs.append(job)

    if "study_guide" in what and week is None and only is None and (select is None or job_key("study_guide", "") in select):
        jobs.append(_guide_job(module, out_root, nc, manifest, jobs, force))
    jobs.sort(key=lambda j: (KIND_ORDER.index(j.kind), j.week or 0, j.title.lower()))
    return Plan(module, out_root, jobs, nc, manifest)


# ---------- study guide ----------
def strip_front_matter(text):
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:].lstrip("\n")
    return text


def guide_inputs(module, out_root):
    """(label, text) for every note and workshop summary on disk, in week order."""
    items = []
    for sub, pattern, rank in (("Lectures", "*.md", 0), ("Workshops", "* - summary.md", 1), ("Coursework", "*.md", 2)):
        d = out_root / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob(pattern)):
            raw = f.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"(?m)^source:\s*(.+)$", raw)
            wk = week_of(m.group(1), Path("")) if m else None
            label = f"{('Lecture', 'Workshop', 'Coursework')[rank]}: {f.stem.removesuffix(' - summary')}" + (f" (week {wk})" if wk else "")
            items.append(((wk or 0, rank, f.name.lower()), f"### {label}\n\n{strip_front_matter(raw).strip()}"))
    return [t for _, t in sorted(items)]


def guide_batches(items, max_in):
    budget = max(4000, (max_in - PROMPT_OVERHEAD) * 4)
    batches, cur, size = [], [], 0
    for it in items:
        for piece in split_text(it, budget):
            if cur and size + len(piece) > budget:
                batches.append(cur)
                cur, size = [], 0
            cur.append(piece)
            size += len(piece)
    if cur:
        batches.append(cur)
    return batches


def _guide_job(module, out_root, nc, manifest, jobs, force):
    version, _ = prompts.get("study_guide", nc["prompts_dir"])
    job = Job("study_guide", f"{module.name} study guide", None, None, out_root / "Study guide.md", version)
    items = guide_inputs(module, out_root)
    pending = [j for j in jobs if j.runs and j.kind in ("lecture_notes", "workshop_summary", "coursework_notes")]
    # tokens: what is on disk + what the pending jobs are expected to add (60% of their output budget)
    est_chars = sum(len(i) for i in items) + sum(int(nc["max_output"][j.kind] * 0.6 * 4) for j in pending if not j.out.exists())
    batches = max(1, -(-est_chars // max(1, (nc["max_input_tokens"] - PROMPT_OVERHEAD) * 4)))
    if not items and not pending:
        job.status, job.note = "kept", "no notes yet: generate lectures/summaries first"
        return job
    rel = job.out.relative_to(module.parent).as_posix()
    job.status = _decide(job, sha(version + "\0" + "\n".join(items)), manifest, rel, force)
    if pending and job.status == "fresh":
        job.status = "refresh"
    if job.status == "kept":
        job.note = "edited by you or not written by the tool (use --force to replace)"
    if job.runs:
        job.in_tokens = est_chars // 4 + PROMPT_OVERHEAD * batches
        job.out_tokens = nc["max_output"]["study_guide"] * batches
        if batches > 1:
            job.in_tokens += int(nc["max_output"]["study_guide"] * 0.6) * batches
            job.out_tokens += nc["max_output"]["study_guide"]
            job.note = f"too long for one request: {batches} partial guides + 1 merge"
        job.note = (job.note + "; " if job.note else "") + "size is an estimate until the notes exist"
    return job


# ---------- reporting ----------
def describe(plan, provider=None, root=None):
    rows = []
    base = plan.module
    for j in plan.jobs:
        rows.append((j.status.upper(), j.kind, j.out.relative_to(base).as_posix(),
                     f"~{j.in_tokens:,} in / ≤{j.out_tokens:,} out" if j.runs else "", j.note))
    lines = [f"Module: {plan.module.name}", ""]
    w = max([len(r[2]) for r in rows] + [10])
    for st, kind, out, tok, note in rows:
        lines.append(f"  {st:<9} {out:<{w}}  {tok}{('  · ' + note) if note else ''}")
    run = plan.runnable()
    lines += ["", f"{len(run)} to generate, {len(plan.jobs) - len(run)} skipped."]
    if run:
        srcs = sorted({j.source.relative_to(plan.module).as_posix() for j in run if j.source})
        lines.append(f"Estimated tokens: ~{plan.in_tokens:,} in, up to {plan.out_tokens:,} out (estimates, ±30%).")
        if provider is not None:
            lines.append(f"Sends the text of {len(srcs)} course file(s)" + (" and your generated notes" if any(j.kind == 'study_guide' for j in run) else "")
                         + f" to: {provider.destination}")
    return "\n".join(lines)


# ---------- generation ----------
def front_matter(module, job, model, source_rel):
    return ("---\n" f"module: {module.name}\n" + (f"source: {source_rel}\n" if source_rel else "")
            + f"generated_by: moodle-app notes ({job.version})\nmodel: {model}\n"
            f"generated: {time.strftime('%Y-%m-%d')}\n" "---\n\n")


def strip_fences(code):
    t = code.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip("\n") + "\n"


def _complete(provider, system, prompt, max_tokens, totals):
    res = provider.complete(system, prompt, max_tokens)
    totals["in"] += res.input_tokens or estimate_tokens(system + prompt)
    totals["out"] += res.output_tokens or estimate_tokens(res.text)
    totals["cached"] += 1 if res.cached else 0
    return res


def _generate(job, plan, provider, totals, log):
    nc, module = plan.cfg, plan.module
    pname = job.kind
    _, tpl = prompts.get(pname, nc["prompts_dir"])
    max_out = nc["max_output"][pname]
    truncated = False
    if job.kind == "study_guide":
        items = guide_inputs(module, plan.out_root)
        if not items:
            return None
        batches = guide_batches(items, nc["max_input_tokens"])
        parts = []
        for i, b in enumerate(batches, 1):
            part = f"(part {i} of {len(batches)}: only these topics; the other parts are written separately)" if len(batches) > 1 else ""
            r = _complete(provider, prompts.SYSTEM, prompts.render(tpl, module=module.name, part=part, text="\n\n".join(b)), max_out, totals)
            truncated |= r.truncated
            parts.append(r.text.strip())
        if len(parts) > 1:
            _, mtpl = prompts.get("study_guide_merge", nc["prompts_dir"])
            r = _complete(provider, prompts.SYSTEM,
                          prompts.render(mtpl, module=module.name, text="\n\n=====\n\n".join(parts)), max_out, totals)
            truncated |= r.truncated
            body = r.text.strip()
        else:
            body = parts[0]
        input_hash = sha(job.version + "\0" + "\n".join(items))
        source_rel = None
    else:
        texts = job.texts
        parts = []
        for i, t in enumerate(texts, 1):
            if len(texts) == 1:
                part = ""
            elif job.kind == "workshop_code":
                part = f"[Part {i} of {len(texts)}: continue the same file; do not repeat the docstring or the __main__ block unless this is the last part.]"
            else:
                part = (f"[Part {i} of {len(texts)} of this document. Cover only this part; use '## Part {i}' as the top heading"
                        + (" and omit the overview" if i > 1 else "") + ".]")
            r = _complete(provider, prompts.SYSTEM,
                          prompts.render(tpl, module=module.name, title=job.title, week=job.week if job.week else "n/a",
                                         source=job.source.name, part=part, text=t), max_out, totals)
            truncated |= r.truncated
            parts.append(r.text)
        body = "\n\n".join(p.strip() for p in parts)
        input_hash = job.input_hash
        source_rel = job.source.relative_to(module).as_posix()

    model = getattr(provider, "model", "")
    if job.kind == "workshop_code":
        code = strip_fences(body)
        head = f"# generated by moodle-app notes ({job.version}, {model}) from {source_rel}\n"
        try:
            compile(code, job.out.name, "exec")
        except SyntaxError as e:
            head += f"# WARNING: the generated code does not parse ({e.msg}, line {e.lineno}). Fix it before running.\n"
            log(f"  ! {job.out.name}: generated code has a syntax error (line {e.lineno}); written with a warning")
        if truncated:
            code += "\n# WARNING: the model hit its output limit; this file may be cut off.\n"
        text = head + code
    else:
        text = front_matter(module, job, model, source_rel) + body + "\n"
        if truncated:
            text += "\n> **Warning:** the model hit its output limit; this file may be cut off. Raise `notes.max_output` and re-run with --force.\n"
    if truncated:
        log(f"  ! {job.out.name}: output hit the token limit and may be cut off")
    return text, input_hash


def execute(plan, provider, store, log=print, force=False, check=None):
    """Generate every runnable job. Returns totals. Manifest and files are written job by job, so an interrupted run keeps its work.
    `check()` is called before each job and may raise to stop the run there."""
    totals = {"in": 0, "out": 0, "cached": 0, "written": 0, "failed": 0, "skipped": 0}
    manifest = store.all()
    ordered = sorted(plan.jobs, key=lambda j: KIND_ORDER.index(j.kind))
    for job in ordered:
        if not job.runs:
            continue
        if check:
            check()
        rel = job.out.relative_to(plan.module.parent).as_posix()
        if job.kind == "study_guide":                            # now the real notes exist: decide again with the real input
            items = guide_inputs(plan.module, plan.out_root)
            if not items:
                log(f"  - {rel}: no notes to condense, skipped")
                totals["skipped"] += 1
                continue
            st = _decide(job, sha(job.version + "\0" + "\n".join(items)), manifest, rel, force)
            if st in ("fresh", "kept"):
                log(f"  = {rel}: {'up to date' if st == 'fresh' else 'kept (edited by you)'}")
                totals["skipped"] += 1
                continue
            job.status = st
        log(f"  > {rel}")
        try:
            out = _generate(job, plan, provider, totals, log)
        except LLMLimit as e:                                       # the plan's limit: stop here, keep what is written
            log(f"  ! {e}")
            totals["stopped"] = str(e)
            break
        except (LLMError, RuntimeError, OSError) as e:
            log(f"  ! {rel}: {e}")
            totals["failed"] += 1
            continue
        if out is None:
            continue
        text, input_hash = out
        if job.out.exists() and job.status == "overwrite":
            shutil.copy2(job.out, job.out.with_name(job.out.name + ".bak"))
            log(f"    previous file kept as {job.out.name}.bak")
        atomic_write(job.out, text)
        with store.c:
            store.put(rel, input_hash, job.version, file_hash(job.out), time.strftime("%Y-%m-%dT%H:%M:%S"))
        manifest[rel] = store.get(rel)
        totals["written"] += 1
    return totals
