"""Everything that talks to the LLM, in one place: the backend of the AI page and of the Study page's assistant.

Only those two pages call these (the server refuses the endpoints without the page's header). Pieces: the module list and
connection state, prompt overrides, the notes plan, a job body for writing notes, and a free question about chosen files."""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..llm.providers import LLMError, LLMLimit, estimate_tokens, make_provider
from ..notes import planner, prompts
from ..notes.extract import SOURCE_SUFFIXES, extract_info, has_text
from . import usage as usage_service
from .notes import run_notes

ASK_SYSTEM = ("You answer questions about course material for a university student. Use only the text given; say so when it "
              "does not contain the answer. Cite pages as (p.N) using the <!-- p.N --> markers. Be concise.")
ASK_MAX_OUT = 2000
STUDY_SYSTEM = ("You are a study assistant for a university student, working inside their notes app. Use the material under CONTEXT "
                "first, and say so when it does not contain the answer. Cite pages as (p.N) where <!-- p.N --> markers exist. "
                "When asked to change or tidy notes, return the complete revised Markdown, ready to paste, with no commentary around it. "
                "For exercises, reason step by step and give hints before a full solution unless the student asks for the solution. "
                "Write maths as LaTeX between $ signs. Be concise.")
CHAT_MAX_OUT = 3000
CHAT_TURNS = 12                          # how much of the conversation is sent along with a new message
CHAT_ITEMS = 20                          # files and text snippets per message
LISTING_CAP = 600
LEGACY = "formatted txt"                 # text bundles written by early versions: never a source, never listed


class AI:
    def __init__(self, ctx, lock=None):
        self.x = ctx
        self.lock = lock or threading.RLock()          # the app's database lock: held for database work only, never during an LLM call

    def _line(self):
        with self.lock:
            return usage_service.line(self.x)

    def _save(self, limits):
        with self.lock:
            usage_service.save_limits(self.x, limits)

    # ---------- what exists
    def _root(self):
        return self.x.root.resolve()

    def _inside(self, rel):
        p = (self._root() / rel).resolve()
        if self._root() not in p.parents and p != self._root():
            raise ValueError("path is outside the study folder")
        return p

    def modules(self):
        from ..domain.years import module_dirs
        return module_dirs(self._root())                     # 'CSCI.323 X', or 'Y3/CSCI.323 X' when the study folder is divided by years

    def _module(self, name):
        if name not in self.modules():
            raise ValueError("pick a module")
        return self._root() / name

    def files(self, module):
        """PDFs and text files in a module (notes included), as paths relative to the study folder."""
        m, out = self._module(module), self.x.settings.notes.get("output_dir", "Notes")
        found = []
        for f in sorted(m.rglob("*")):
            parts = f.relative_to(m).parts
            if f.is_file() and f.suffix.lower() in SOURCE_SUFFIXES and not any(x.startswith((".", "_")) for x in parts) \
                    and LEGACY not in parts:
                found.append(f.relative_to(self._root()).as_posix())
            if len(found) >= LISTING_CAP:
                break
        return found

    # ---------- what has been generated
    def generated(self):
        """Per module: the notes (with whether you edited them), and the deadlines saved from notes."""
        root = self._root()
        manifest = {k: v for k, v in self.x.notes_manifest.all().items()}
        out_name = self.x.settings.notes.get("output_dir", "Notes")
        mods = []
        for name in self.modules():
            m = root / name
            notes = []
            nd = m / out_name
            if nd.is_dir():
                for f in sorted(nd.rglob("*")):
                    if f.is_file() and not f.name.endswith(".bak") and not f.name.startswith("."):
                        info = self._info(f, root)
                        rel = f"{name}/{f.relative_to(m).as_posix()}"
                        mf = manifest.get(rel)
                        info["state"] = "yours" if not mf else "generated"
                        notes.append(info)
            if notes:
                mods.append({"module": name, "notes": notes})
        return {"modules": mods, "deadlines": self.saved_deadlines()}

    @staticmethod
    def _info(f, root):
        st = f.stat()
        return {"rel": f.relative_to(root).as_posix(), "name": f.name, "size": st.st_size, "mtime": st.st_mtime}

    # ---------- deadlines found in coursework notes are kept as calendar events
    DEADLINE_RE = re.compile(r"<!--\s*deadlines:\s*(\[.*?\])\s*-->", re.S)

    def _course_id(self, module):
        for c in self.x.courses.all():
            if (c["folder_rel"] or "") == module:
                return c["id"]
        return None

    def save_deadlines(self):
        """Read the deadline line of every coursework note and add the dates to your calendar (once each). Returns how many are new."""
        root, out_name = self._root(), self.x.settings.notes.get("output_dir", "Notes")
        tz = ZoneInfo(self.x.settings.timezone)
        added = 0
        for name in self.modules():
            cw = root / name / out_name / "Coursework"
            if not cw.is_dir():
                continue
            cid = self._course_id(name)
            for f in sorted(cw.glob("*.md")):
                m = self.DEADLINE_RE.search(f.read_text(encoding="utf-8", errors="replace"))
                if not m:
                    continue
                try:
                    items = json.loads(m.group(1))
                except ValueError:
                    continue
                for it in items if isinstance(items, list) else []:
                    try:
                        title, due, tm = str(it["title"]).strip(), str(it["due"]).strip(), str(it.get("time") or "").strip()
                        dt = datetime.strptime(due + (" " + tm if tm else ""), "%Y-%m-%d" + (" %H:%M" if tm else "")).replace(tzinfo=tz)
                    except (KeyError, ValueError, TypeError):
                        continue
                    ts, note = int(dt.timestamp()), f"AI: {f.relative_to(root).as_posix()}"
                    with self.lock, self.x.conn:
                        have = any(e["title"] == title and e["start_ts"] == ts and e["course_id"] == cid and (e["note"] or "").startswith("AI:")
                                   for e in self.x.events.all())
                        if not have:
                            self.x.events.add(title, ts, None, not tm, cid, note, self.x.ts())
                            added += 1
        return added

    def saved_deadlines(self):
        with self.lock:
            return [{"id": e["id"], "title": e["title"], "start_ts": e["start_ts"], "all_day": bool(e["all_day"]), "course": e["course_short"],
                     "from": (e["note"] or "")[4:]} for e in self.x.events.all() if (e["note"] or "").startswith("AI:")]

    def remove_deadline(self, id_):
        with self.lock, self.x.conn:
            e = self.x.events.get(id_)
            if not e or not (e["note"] or "").startswith("AI:"):
                raise ValueError("not a deadline saved from notes")
            self.x.events.delete(id_)

    # ---------- connection
    def provider(self, cached=True):
        s = self.x.settings
        with self.lock:
            known = usage_service.load_limits(self.x)
        return make_provider(s.llm, s.home, cache_dir=(s.home / "cache" / "llm") if cached else None, limits=known)

    def state(self):
        out = {"modules": self.modules(), "usage": self._line(), "prompts": self.prompts(), "root": self._root().name,
               "what": ["lectures", "summaries", "code", "coursework", "course"]}
        try:
            p = self.provider()
            out.update(to=p.destination, limit_problem=p.limit_problem())
        except LLMError as e:
            out["error"] = str(e)
        return out

    def check(self):
        """One tiny call to learn where the plan stands (and to prove the connection works)."""
        p = self.provider(cached=False)
        if p.runnable_probe():
            p.probe()
        else:
            p.complete("Answer with one word.", "Say: ready", 20)
        self._save(p.limits)
        return {"usage": self._line(), "to": p.destination, "limit_problem": p.limit_problem()}

    # ---------- prompts
    def _pdir(self):
        n = self.x.settings.notes
        if not n.get("prompts_dir"):
            n["prompts_dir"] = str(self.x.settings.home / "prompts")
        return Path(n["prompts_dir"]).expanduser()

    def prompts(self):
        out = []
        for name, (_, default) in prompts.DEFAULTS.items():
            version, text = prompts.get(name, self._pdir())
            out.append({"name": name, "text": text, "default": default, "custom": version.startswith("custom-"), "version": version})
        return out

    def save_prompt(self, name, text):
        if name not in prompts.DEFAULTS:
            raise ValueError("unknown prompt")
        f = self._pdir() / f"{name}.md"
        if text is None or text.strip() == prompts.DEFAULTS[name][1].strip():     # same as the built-in: drop the override
            f.unlink(missing_ok=True)
        else:
            if "{{text}}" not in text:
                raise ValueError("the prompt must contain {{text}}, where the course text goes")
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
        return self.prompts()

    # ---------- notes
    def sources(self, module, rels):
        """The exact files picked in the app, or None for 'everything in the module'."""
        if not rels:
            return None
        m, out = self._module(module), self.x.settings.notes.get("output_dir", "Notes")
        files = set()
        for r in rels:
            p = self._inside(r)
            for f in ([p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]):
                parts = f.relative_to(m).parts if m in f.parents else None
                if parts is None:
                    raise ValueError("pick files from the chosen module")
                if f.suffix.lower() in SOURCE_SUFFIXES and not any(x.startswith((".", "_")) or x in (LEGACY, out) for x in parts):
                    files.add(f)
        if not files:
            raise ValueError("no PDFs or text files in the selection")
        return files

    ALL = "*"

    def _scope(self, module):
        return self.modules() if module == self.ALL else [self._module(module).name]

    def plan(self, module, what, week, rels, force=False):
        """The plan as an editable list of jobs. module '*' = every module in the study folder (rels are ignored there)."""
        s = self.x.settings
        self._pdir()
        names = self._scope(module)
        kinds = planner.parse_what(what or "lectures,summaries")
        try:
            p = self.provider()
        except LLMError as e:
            return {"error": str(e)}
        jobs, texts, skipped = [], [], 0
        for name in names:
            mod = self._module(name)
            only = self.sources(name, rels) if module != self.ALL else None
            with self.lock:
                plan = planner.build_plan(mod, kinds, week, s.notes, self.x.notes_manifest.all(), force=force, only=only, log=lambda m: None)
            for j in plan.jobs:
                if not j.runs:
                    skipped += 1
                    continue
                jobs.append({"module": name, "key": planner.job_key(j.kind, j.source.relative_to(mod).as_posix() if j.source else ""),
                             "kind": j.kind, "title": j.title, "week": j.week, "status": j.status, "note": j.note,
                             "out": j.out.relative_to(mod).as_posix(), "in_tokens": j.in_tokens, "out_tokens": j.out_tokens})
            if any(j.runs for j in plan.jobs):
                texts.append(planner.describe(plan, p))
        if usage_service.stale(p.limits) and p.runnable_probe():
            p.probe()
            self._save(p.limits)
        return {"usage": self._line(), "limit_problem": p.limit_problem(), "jobs": jobs, "skipped": skipped, "text": "\n\n".join(texts),
                "runnable": len(jobs), "in_tokens": sum(j["in_tokens"] for j in jobs), "out_tokens": sum(j["out_tokens"] for j in jobs),
                "to": p.destination, "scope": "all" if module == self.ALL else "module"}

    def notes_job(self, module, what, week, rels, force=False, select=None):
        """select: [{module, key}] = the plan as you edited it (None = everything the plan lists)."""
        self._pdir()
        names = self._scope(module)
        by_mod = {}
        for it in select or []:
            by_mod.setdefault(it["module"], set()).add(it["key"])
        if select is not None and not by_mod:
            raise ValueError("nothing is ticked in the plan")
        todo = [(self._module(n), self.sources(n, rels) if module != self.ALL else None, by_mod.get(n) if select is not None else None)
                for n in names if select is None or n in by_mod]

        def work():
            failed = False
            for mod, only, sel in todo:
                if len(todo) > 1:
                    print(f"=== {mod.name} ===")
                code = run_notes(self.x, str(mod), what=what or "lectures,summaries", week=week, yes=True, force=force, only=only, select=sel)
                if code:
                    failed = True
                    if self._limit_hit():
                        break                                         # the plan's limit: no point trying the next module
            n = self.save_deadlines()                                 # whatever was written before a stop still counts
            if n:
                print(f"Saved {n} deadline(s) from the coursework notes to your calendar (AI page > Generated files).")
            if failed:
                print("! notes did not finish (see above)")
        return work

    def _limit_hit(self):
        with self.lock:
            p = self.provider()
        return bool(p.limit_problem())

    # ---------- a free question
    def chat(self, messages, rels=(), snippets=()):
        """The Study page's assistant: one answer to the last message, given the conversation so far and the context you attached.

        `rels` are files in the study folder (PDFs are read through the shared text cache, so nothing is converted twice);
        `snippets` are {label, text} pieces passed by the page: a selection, pasted text, or the unsaved text of an open file."""
        msgs = [{"role": m.get("role"), "text": str(m.get("text") or "").strip()} for m in (messages or []) if isinstance(m, dict)]
        msgs = [m for m in msgs if m["role"] in ("user", "assistant") and m["text"]][-CHAT_TURNS:]
        if not msgs or msgs[-1]["role"] != "user":
            raise ValueError("type a message")
        p = self.provider(cached=False)
        if p.limit_problem():
            raise LLMLimit(p.limit_problem())
        left = self.x.settings.notes.get("max_input_tokens", 24000) * 4
        parts, used, truncated = [], [], False

        def add(label, text):
            nonlocal left, truncated
            if left <= 0:
                truncated = True
                return
            if len(text) > left:
                text, truncated = text[:left], True
            left -= len(text)
            parts.append(f"=== {label} ===\n{text}")
            used.append(label)

        for sn in list(snippets or [])[:CHAT_ITEMS]:
            if isinstance(sn, dict) and str(sn.get("text") or "").strip():
                add(str(sn.get("label") or "Text")[:200], str(sn["text"]))
        for r in list(rels or [])[:CHAT_ITEMS]:
            f = self._inside(r)
            if not f.is_file() or f.suffix.lower() not in SOURCE_SUFFIXES:
                raise ValueError(f"{Path(r).name} is not a PDF or text file")
            text = extract_info(f)[0]
            add(f"FILE: {f.name}", text if has_text(text) else "(no text found in this file)")
        history = "\n\n".join(("Student: " if m["role"] == "user" else "Assistant: ") + m["text"] for m in msgs[:-1])[-16000:]
        prompt = (("CONTEXT\n\n" + "\n\n".join(parts) + "\n\n") if parts else "") \
            + (("CONVERSATION SO FAR\n\n" + history + "\n\n") if history else "") + "Student: " + msgs[-1]["text"]
        res = p.complete(STUDY_SYSTEM, prompt, CHAT_MAX_OUT)
        self._save(p.limits)
        return {"answer": res.text, "context": used, "truncated": truncated or res.truncated, "to": p.destination,
                "in_tokens": res.input_tokens or estimate_tokens(prompt), "out_tokens": res.output_tokens or estimate_tokens(res.text),
                "usage": self._line()}

    def ask(self, question, rels, system=None):
        question = (question or "").strip()
        if not question:
            raise ValueError("type a question")
        p = self.provider(cached=False)
        if p.limit_problem():
            raise LLMLimit(p.limit_problem())
        budget = self.x.settings.notes.get("max_input_tokens", 24000) * 4
        parts, used, left, truncated = [], [], budget, False
        for r in rels[:20]:
            f = self._inside(r)
            if not f.is_file() or f.suffix.lower() not in SOURCE_SUFFIXES:
                raise ValueError(f"{Path(r).name} is not a PDF or text file")
            text = extract_info(f)[0]
            if not has_text(text):
                text = "(no text found in this file)"
            if len(text) > left:
                text, truncated = text[:max(left, 0)], True
            left -= len(text)
            parts.append(f"=== FILE: {f.name} ===\n{text}")
            used.append(f.name)
        prompt = (("Course text:\n\n" + "\n\n".join(parts) + "\n\n") if parts else "") + f"Question: {question}"
        res = p.complete(system or ASK_SYSTEM, prompt, ASK_MAX_OUT)
        self._save(p.limits)
        return {"answer": res.text, "files": used, "truncated": truncated or res.truncated,
                "in_tokens": res.input_tokens or estimate_tokens(prompt), "out_tokens": res.output_tokens or estimate_tokens(res.text),
                "usage": self._line()}
