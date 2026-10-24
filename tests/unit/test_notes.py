import json
from pathlib import Path

import pymupdf
import pytest

from moodle_app import config, db
from moodle_app.db import repos
from moodle_app.llm.providers import Result, Provider
from moodle_app.notes import planner as notes, prompts
from moodle_app.notes.extract import extract, split_text, clean_pages
from moodle_app.services import notes as notes_service
from moodle_app.services.context import Ctx
import json


def _ctx(root, cfg, log):
    home = Path(root).parent / "home"
    home.mkdir(exist_ok=True)
    (home / "config.json").write_text(json.dumps({"dest_root": str(root), **(cfg or {})}))
    s = config.load(home)
    conn = db.connect(s.db_path)
    db.migrate(conn)
    return Ctx(s, conn, None, log=log)


class notes_cli:
    """Adapter so the tests read like calls: run(module, cfg, root, here, **options)."""
    @staticmethod
    def run(name, cfg, root, here, log=print, **kw):
        return notes_service.run_notes(_ctx(root, cfg, log), name, **kw)

    @staticmethod
    def resolve_module(root, name):
        try:
            return notes_service.resolve_module(root, name)
        except ValueError as e:
            raise SystemExit(str(e))


def store_for(module):
    ctx = _ctx(module.parent, {}, lambda *_: None)
    return ctx.notes_manifest


def make_pdf(path, pages):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for t in pages:
        pg = doc.new_page()
        for i, line in enumerate((t or "").split("\n") if t else []):
            pg.insert_text((72, 72 + 12 * i), line, fontsize=9)
    doc.save(str(path))
    doc.close()


class Fake(Provider):
    name, model, remote, base_url = "fake", "fake-1", False, "http://localhost:1"

    def __init__(self, code=None):
        self.calls, self.code = [], code

    def complete(self, system, prompt, max_tokens):
        self.calls.append(prompt)
        if "Python file" in prompt:
            return Result(self.code if self.code is not None else "```python\ndef q1():\n    raise NotImplementedError\n```", 50, 20)
        return Result("# Notes\n\nbody (p.1)", 100, 30)


@pytest.fixture
def module(tmp_path):
    m = tmp_path / "CSCI.999 Test Module"
    make_pdf(m / "Lectures" / "Week 01" / "01 Intro.pdf", ["Intro slide text", "Second slide"])
    make_pdf(m / "Lectures" / "Week 02" / "02 Trees.pdf", ["Trees are graphs"])
    make_pdf(m / "Workshops" / "Week 01" / "Workshop 1.pdf", ["Q1. Write f(x)."])
    make_pdf(m / "Lectures" / "_old_versions" / "old.pdf", ["old"])
    make_pdf(m / "Exams" / "mock.pdf", ["exam"])
    return m


def plan_of(m, what="all", week=None, **kw):
    return notes.build_plan(m, notes.parse_what(what), week, kw.get("cfg"), store_for(m).all(), force=kw.get("force", False))


def test_extract_page_markers_and_image_only(tmp_path):
    p = tmp_path / "a.pdf"
    make_pdf(p, ["hello", "", "world"])
    t = extract(p)
    assert "<!-- p.1 -->\nhello" in t and "<!-- p.2: no text (image only) -->" in t and "<!-- p.3 -->\nworld" in t


def test_clean_pages_removes_repeated_headers_and_numbers():
    pages = [f"COURSE HEADER\nbody {i}\n{i}" for i in range(1, 6)]
    out = clean_pages(pages)
    assert all("COURSE HEADER" not in p for p in out) and out[2] == "body 3"


def test_split_text_roundtrip():
    text = "".join(f"<!-- p.{i} -->\n" + "x" * 90 + "\n" for i in range(1, 30))
    chunks = split_text(text, 500)
    assert len(chunks) > 3 and "".join(chunks) == text and all(len(c) <= 500 for c in chunks)
    long_page = "<!-- p.1 -->\n" + "\n".join("y" * 40 for _ in range(100))
    c2 = split_text(long_page, 300)
    assert "".join(c2) == long_page and all(len(c) <= 300 for c in c2)


def test_plan_discovers_only_course_material(module):
    p = plan_of(module)
    outs = sorted(j.out.relative_to(module).as_posix() for j in p.jobs)
    assert outs == ["Notes/Lectures/01 Intro.md", "Notes/Lectures/02 Trees.md", "Notes/Study guide.md",
                    "Notes/Workshops/Workshop 1 - summary.md", "Notes/Workshops/Workshop_1.py"]
    kinds = [j.kind for j in p.jobs]
    assert kinds.count("lecture_notes") == 2 and kinds.count("workshop_summary") == 1 and kinds.count("workshop_code") == 1
    assert "old.pdf" not in " ".join(str(j.source) for j in p.jobs) and "mock.pdf" not in " ".join(str(j.source) for j in p.jobs)
    assert not (module / "Notes").exists(), "planning must not write anything"


def test_week_filter_and_guide_skipped(module):
    p = plan_of(module, week=2)
    assert [j.kind for j in p.jobs] == ["lecture_notes"] and p.jobs[0].week == 2


def test_dry_run_calls_nothing_and_writes_nothing(module):
    f = Fake()
    logs = []
    rc = notes_cli.run("CSCI.999", {}, module.parent, module.parent, dry_run=True, provider=f, log=logs.append)
    assert rc == 0 and f.calls == [] and not (module / "Notes").exists()
    assert "Dry run" in logs[-1] and "fake" in "\n".join(logs)


def test_generate_lectures_workshops_and_guide(module):
    f = Fake()
    rc = notes_cli.run("CSCI.999", {}, module.parent, module.parent, yes=True, provider=f, log=lambda *_: None)
    assert rc == 0
    n = module / "Notes"
    lec = (n / "Lectures" / "01 Intro.md").read_text()
    assert lec.startswith("---\nmodule: CSCI.999 Test Module\nsource: Lectures/Week 01/01 Intro.pdf") and "body (p.1)" in lec
    code = (n / "Workshops" / "Workshop_1.py").read_text()
    assert "```" not in code and "def q1()" in code and code.startswith("# generated by moodle-app")
    compile(code, "x", "exec")
    assert (n / "Workshops" / "Workshop 1 - summary.md").exists() and (n / "Study guide.md").exists()
    guide_prompt = f.calls[-1]
    assert "Lecture: 01 Intro (week 1)" in guide_prompt and "Workshop: Workshop 1 (week 1)" in guide_prompt
    assert "module: CSCI.999" not in guide_prompt, "front matter must not be sent to the guide"
    assert set(store_for(module).all()) >= {"CSCI.999 Test Module/Notes/Lectures/01 Intro.md", "CSCI.999 Test Module/Notes/Study guide.md"}


def test_second_run_is_free(module):
    f = Fake()
    args = ("CSCI.999", {}, module.parent, module.parent)
    notes_cli.run(*args, yes=True, provider=f, log=lambda *_: None)
    n = len(f.calls)
    logs = []
    notes_cli.run(*args, yes=True, provider=f, log=logs.append)
    assert len(f.calls) == n and "0 to generate" in "\n".join(logs)


def test_edited_file_is_never_overwritten(module):
    f = Fake()
    args = ("CSCI.999", {}, module.parent, module.parent)
    notes_cli.run(*args, what="lectures", yes=True, provider=f, log=lambda *_: None)
    mine = module / "Notes" / "Lectures" / "01 Intro.md"
    mine.write_text(mine.read_text() + "\nMY OWN ADDITION\n")
    # source changes -> would normally refresh, but the file is edited
    make_pdf(module / "Lectures" / "Week 01" / "01 Intro.pdf", ["changed"])
    logs = []
    notes_cli.run(*args, what="lectures", yes=True, provider=f, log=logs.append)
    assert "MY OWN ADDITION" in mine.read_text() and "KEPT" in "\n".join(logs)
    # --force replaces it but keeps a backup
    notes_cli.run(*args, what="lectures", yes=True, force=True, provider=f, log=lambda *_: None)
    assert "MY OWN ADDITION" not in mine.read_text() and "MY OWN ADDITION" in (mine.with_name(mine.name + ".bak")).read_text()


def test_untouched_file_refreshes_when_source_changes(module):
    f = Fake()
    args = ("CSCI.999", {}, module.parent, module.parent)
    notes_cli.run(*args, what="lectures", yes=True, provider=f, log=lambda *_: None)
    n = len(f.calls)
    make_pdf(module / "Lectures" / "Week 02" / "02 Trees.pdf", ["Trees are graphs", "and a new slide"])
    notes_cli.run(*args, what="lectures", yes=True, provider=f, log=lambda *_: None)
    assert len(f.calls) == n + 1


def test_file_not_written_by_tool_is_kept(module):
    (module / "Notes" / "Lectures").mkdir(parents=True)
    (module / "Notes" / "Lectures" / "01 Intro.md").write_text("handwritten")
    f = Fake()
    notes_cli.run("CSCI.999", {}, module.parent, module.parent, what="lectures", yes=True, provider=f, log=lambda *_: None)
    assert (module / "Notes" / "Lectures" / "01 Intro.md").read_text() == "handwritten" and len(f.calls) == 1


def test_confirmation_gate(module):
    args = ("CSCI.999", {}, module.parent, module.parent)
    # small run without the guide: no question asked, even non-interactively
    f2 = Fake()
    assert notes_cli.run(*args, what="lectures", provider=f2, interactive=False, log=lambda *_: None) == 0 and f2.calls
    # guide always asks; non-interactive without --yes cancels and sends nothing
    f = Fake()
    assert notes_cli.run(*args, what="guide", provider=f, interactive=False, log=lambda *_: None) == 1 and f.calls == []
    assert notes_cli.run(*args, what="guide", provider=f, interactive=True, input_fn=lambda q: "n", log=lambda *_: None) == 1 and f.calls == []
    assert notes_cli.run(*args, what="guide", provider=f, interactive=True, input_fn=lambda q: "y", log=lambda *_: None) == 0 and f.calls


def test_token_threshold_triggers_confirmation(module):
    f = Fake()
    cfg = {"notes": {"confirm_tokens": 10}}
    rc = notes_cli.run("CSCI.999", cfg, module.parent, module.parent, what="lectures", provider=f, interactive=False, log=lambda *_: None)
    assert rc == 1 and f.calls == []


def test_long_source_is_split_and_guide_batched(module):
    make_pdf(module / "Lectures" / "Week 03" / "03 Big.pdf", ["\n".join(" ".join(f"w{i}x{k}y{j}" for j in range(8)) for k in range(55)) for i in range(10)])
    cfg = {"notes": {"max_input_tokens": 3000}}
    f = Fake()
    p = notes.build_plan(module, notes.parse_what("lectures"), None, cfg["notes"])
    big = next(j for j in p.jobs if j.title == "03 Big")
    assert len(big.texts) > 1 and "split" in big.note and "".join(big.texts) == extract(big.source)
    notes.execute(p, f, store_for(module), log=lambda *_: None)
    assert sum("Part 2 of" in c for c in f.calls) >= 1
    # guide larger than the budget -> partial guides + merge
    notes.execute(notes.build_plan(module, notes.parse_what("guide"), None, cfg["notes"], store_for(module).all()), f, store_for(module), log=lambda *_: None)
    assert (module / "Notes" / "Study guide.md").exists()


def test_guide_batches_and_merge(module):
    class Long(Fake):
        def complete(self, system, prompt, max_tokens):
            r = super().complete(system, prompt, max_tokens)
            return Result("line of notes\n" * 300 if "Partial" not in prompt and "Condense" not in prompt else "GUIDE PART", 1, 1)
    f = Long()
    cfg = {"notes": {"max_input_tokens": 2000}}
    notes.execute(notes.build_plan(module, notes.parse_what("lectures"), None, cfg["notes"]), f, store_for(module), log=lambda *_: None)
    p = notes.build_plan(module, notes.parse_what("guide"), None, cfg["notes"])
    assert "partial guides" in p.jobs[0].note
    f.calls.clear()
    notes.execute(p, f, store_for(module), log=lambda *_: None)
    assert sum("Condense" in c for c in f.calls) >= 2 and "Partial guides" in f.calls[-1]
    assert (module / "Notes" / "Study guide.md").read_text().endswith("GUIDE PART\n")


def test_guide_without_notes_is_skipped(module):
    p = plan_of(module, what="guide")
    assert p.jobs[0].status == "kept" and "generate lectures" in p.jobs[0].note


def test_bad_code_is_written_with_warning(module):
    f = Fake(code="def broken(:\n  pass")
    notes_cli.run("CSCI.999", {}, module.parent, module.parent, what="code", yes=True, provider=f, log=lambda *_: None)
    assert "does not parse" in (module / "Notes" / "Workshops" / "Workshop_1.py").read_text()


def test_prompt_override_and_single_pass_render(tmp_path, module):
    d = tmp_path / "pr"
    d.mkdir()
    (d / "lecture_notes.md").write_text("CUSTOM {{title}} :: {{text}}")
    v, tpl = prompts.get("lecture_notes", d)
    assert v.startswith("custom-")
    assert prompts.render(tpl, title="T", text="has {{title}} inside") == "CUSTOM T :: has {{title}} inside"
    f = Fake()
    notes_cli.run("CSCI.999", {"notes": {"prompts_dir": str(d)}}, module.parent, module.parent, what="lectures", yes=True,
                  provider=f, log=lambda *_: None)
    assert f.calls[0].startswith("CUSTOM 01 Intro")


def test_scanned_pdf_without_text_is_not_sent(module):
    make_pdf(module / "Lectures" / "Week 04" / "04 Scan.pdf", ["", ""])
    p = plan_of(module, what="lectures")
    job = next(j for j in p.jobs if j.title == "04 Scan")
    assert job.status == "kept" and "no text" in job.note


def test_resolve_module(module):
    assert notes_cli.resolve_module(module.parent, "csci.999") == module
    with pytest.raises(SystemExit):
        notes_cli.resolve_module(module.parent, "nothing")


def test_usage_line_shows_plan_windows(tmp_path):
    from helpers import close_env, make_env
    from moodle_app.services import usage
    e = make_env(tmp_path)
    try:
        assert "not known yet" in usage.line(e.ctx)
        now = e.ctx.clock()
        usage.save_limits(e.ctx, {"seen": now, "status": "allowed", "overage": "rejected", "using_overage": False,
                                  "windows": {"five_hour": {"util": 0.42, "resets": now + 3600, "status": "allowed"},
                                              "seven_day": {"util": 0.1, "resets": now + 86400, "status": "allowed"}}})
        ln = usage.line(e.ctx)
        assert "5-hour 42%" in ln and "7-day 10%" in ln and "extra usage off" in ln
        assert not usage.stale(usage.load_limits(e.ctx), now)
    finally:
        close_env(e)
