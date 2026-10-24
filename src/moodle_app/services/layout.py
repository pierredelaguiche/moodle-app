"""Organise the study folder from the app: new folders, moves that keep Moodle files tracked, rename, subdivide by week,
and a saved destination for Moodle files that are not downloaded yet.

Rules: every path is relative to dest_root, never leaves it, and never goes through a symbolic link; nothing is overwritten
(a name that is taken is skipped and reported); module folders (the ones Moodle courses are matched to) cannot be moved,
renamed or removed; after a move the database follows the file, so a file you moved is *moved*, never "missing" or "new".
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from ..domain.util import safe_name
from ..domain.weeks import week_dir, week_for

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WEEK_WORD = re.compile(r"(?:week|wk)[\s_\-]*0*(\d{1,2})(?!\d)", re.I)
SESSION_WORD = re.compile(r"(?:lectures?|lec|workshops?|ws|labs?|seminars?|tutorials?|practicals?|worksheets?|exercises?)[\s_\-]*0*(\d{1,2})(?!\d)", re.I)


class LayoutError(ValueError):
    pass


def clean_name(name):
    n = (name or "").strip()
    if not n or n in (".", "..") or n.startswith(".") or _BAD.search(n) or len(n) > 200 or n.endswith("."):
        raise LayoutError(f"'{name}' is not a usable folder or file name (no slashes or leading dot)")
    return n


def parent_of(rel):
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


class Layout:
    def __init__(self, ctx):
        self.x = ctx

    @property
    def root(self) -> Path:
        return self.x.root.resolve()

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    # ------------------------------------------------------------------ paths
    def _norm(self, rel):
        rel = (rel or "").strip().strip("/")
        if "\0" in rel:
            raise LayoutError("bad path")
        parts = [p for p in rel.split("/")] if rel else []
        if any(p in ("", ".", "..") for p in parts) or any(p.startswith(".") for p in parts):
            raise LayoutError("that path cannot be used")
        return rel

    def abs(self, rel, must_exist=True) -> Path:
        rel = self._norm(rel)
        cur = self.root
        for part in (rel.split("/") if rel else []):
            cur = cur / part
            if cur.is_symlink():
                raise LayoutError("symbolic links are not followed")
        if must_exist and not cur.exists():
            raise LayoutError(f"'{rel}' does not exist")
        return cur

    def protected(self):
        """Module folders: Moodle courses are matched to these by path."""
        return {c["folder_rel"] for c in self.x.courses.all() if c["folder_rel"]}

    def _is_protected(self, rel):
        return any(p == rel or p.startswith(rel + "/") for p in self.protected())

    # ------------------------------------------------------------------ folders
    def _ensure(self, rel):
        """Create `rel` and any missing parents. Returns the list of folders that were created."""
        rel = self._norm(rel)
        created, cur = [], self.root
        for part in (rel.split("/") if rel else []):
            clean_name(part)
            cur = cur / part
            if cur.is_symlink():
                raise LayoutError("symbolic links are not followed")
            if cur.exists():
                if not cur.is_dir():
                    raise LayoutError(f"'{part}' is a file, not a folder")
            else:
                cur.mkdir()
                created.append(self.rel(cur))
        return created

    def mkdir(self, rel):
        rel = self._norm(rel)
        if not rel:
            raise LayoutError("give the new folder a name")
        if self.abs(rel, must_exist=False).exists():
            raise LayoutError(f"'{rel.rsplit('/', 1)[-1]}' already exists")
        return {"rel": rel, "created": self._ensure(rel)}

    def rmdir(self, rel):
        rel = self._norm(rel)
        p = self.abs(rel)
        if not rel or not p.is_dir():
            raise LayoutError("not a folder")
        if self._is_protected(rel):
            raise LayoutError("this is a module folder that Moodle files are matched to")
        kids = [k for k in p.iterdir() if k.name != ".DS_Store"]
        if kids:
            raise LayoutError("the folder is not empty; move or delete what is inside first")
        (p / ".DS_Store").unlink(missing_ok=True)
        p.rmdir()
        return {"removed": rel}

    # ------------------------------------------------------------------ moves
    def _after_move(self, old, new):
        x = self.x
        with x.conn:
            changed = x.files.rewrite_paths(old, new)
            x.notes_manifest.rewrite(old, new)
            x.targets.rewrite(old, new)
            for key, cid, status, op, np in changed:
                if status == "pending":                              # keep the planned place through the next sync
                    x.targets.set(key, parent_of(np))
                x.change("moved", cid, key, {"from": op, "to": np, "by": "you"})
        for e in x.pending.values():                                 # downloads queued in memory follow too
            try:
                cur = e.dest.relative_to(self.root).as_posix()
            except ValueError:
                continue
            if cur == old or cur.startswith(old + "/"):
                e.dest = self.root / (new + cur[len(old):])

    def _do_move(self, old, new):
        """Move one file or folder to the exact path `new` (parents are created). Nothing is overwritten."""
        src = self.abs(old)
        if not old:
            raise LayoutError("the study folder itself cannot be moved")
        if self._is_protected(old):
            raise LayoutError("this is a module folder that Moodle files are matched to")
        if src.is_dir() and (new == old or new.startswith(old + "/")):
            raise LayoutError("a folder cannot go inside itself")
        dst = self.abs(new, must_exist=False)
        if dst.exists() or dst.is_symlink():
            raise LayoutError(f"'{dst.name}' already exists there")
        created = self._ensure(parent_of(new))
        try:
            os.rename(src, dst)
        except OSError:
            shutil.move(str(src), str(dst))
        self._after_move(old, new)
        return created

    def move(self, rels, dst):
        """Move files and folders into the folder `dst`. Returns {moved:[{from,to,dir}], skipped:[{rel,reason}], created:[...]}."""
        dst = self._norm(dst)
        dp = self.abs(dst, must_exist=False)
        if dp.exists() and not dp.is_dir():
            raise LayoutError("the destination is a file")
        for part in (dst.split("/") if dst else []):
            clean_name(part)
        moved, skipped, created, good = [], [], [], set()
        for r in rels:
            try:
                good.add(self._norm(r))
            except LayoutError as e:
                skipped.append({"rel": str(r), "reason": str(e)})
        items = sorted(good)
        items = [r for r in items if not any(r.startswith(o + "/") for o in items if o != r)]   # a moved folder takes its contents along
        for rel in items:
            try:
                src = self.abs(rel)
                if parent_of(rel) == dst:
                    skipped.append({"rel": rel, "reason": "already in that folder", "noop": True})
                    continue
                new = f"{dst}/{src.name}" if dst else src.name
                is_dir = src.is_dir()
                created += self._do_move(rel, new)
                moved.append({"from": rel, "to": new, "dir": is_dir})
            except (LayoutError, OSError) as e:
                skipped.append({"rel": rel, "reason": str(e)})
        return {"moved": moved, "skipped": skipped, "created": sorted(set(created))}

    def rename(self, rel, name):
        rel = self._norm(rel)
        name = clean_name(name)
        new = f"{parent_of(rel)}/{name}" if parent_of(rel) else name
        if new == rel:
            return {"moved": [], "skipped": [], "created": []}
        src_is_dir = self.abs(rel).is_dir()
        created = self._do_move(rel, new)
        return {"moved": [{"from": rel, "to": new, "dir": src_is_dir}], "skipped": [], "created": created}

    def undo(self, moved, created=()):
        """Put things back exactly where they were; then remove the folders that the action had created (only if empty)."""
        restored, skipped = [], []
        for m in reversed(list(moved)):
            try:
                self._do_move(self._norm(m["to"]), self._norm(m["from"]))
                restored.append(m["from"])
            except (LayoutError, OSError, KeyError) as e:
                skipped.append({"rel": m.get("to", ""), "reason": str(e)})
        for rel in sorted(created, key=lambda r: -r.count("/")):
            try:
                self.rmdir(rel)
            except (LayoutError, OSError):
                pass
        return {"restored": restored, "skipped": skipped}

    # ------------------------------------------------------------------ subdivide by week
    def _week_for(self, rec, name):
        if rec is not None:
            wk, why = week_for(rec.section_no, rec.sec_name, name, rec.moodle_name, lead=True)
            if wk:
                return wk, "Moodle week" if why == "section index" else why
        m = WEEK_WORD.search(name) or SESSION_WORD.search(name)
        if m and 0 < int(m.group(1)) <= 60:
            return int(m.group(1)), "name"
        return None

    def subdivide_plan(self, rel):
        rel = self._norm(rel)
        folder = self.abs(rel)
        if not folder.is_dir():
            raise LayoutError("not a folder")
        downloaded = {r.rel_path: r for r in self.x.files.all() if r.status == "downloaded"}
        weeks, left = {}, []
        for child in sorted(folder.iterdir(), key=lambda c: c.name.lower()):
            if child.name.startswith(".") or child.is_symlink() or not child.is_file():
                continue
            crel = self.rel(child)
            wk = self._week_for(downloaded.get(crel), child.name)
            if wk:
                weeks.setdefault(wk[0], []).append({"rel": crel, "name": child.name, "by": wk[1]})
            else:
                left.append({"rel": crel, "name": child.name})
        waiting = [r for r in self.x.files.all() if r.status in ("pending", "ignored") and parent_of(r.rel_path) == rel and r.section_no > 0]
        out = []
        for n in sorted(weeks):
            wd = week_dir(folder.parent, folder.name, n)
            out.append({"week": n, "dir": wd.name, "exists": wd.is_dir(), "files": weeks[n]})
        return {"folder": rel, "weeks": out, "left": left, "waiting": len(waiting),
                "count": sum(len(w["files"]) for w in out)}

    def subdivide(self, rel):
        plan = self.subdivide_plan(rel)
        folder = self.abs(plan["folder"])
        moved, skipped, created = [], [], []
        for w in plan["weeks"]:
            target = week_dir(folder.parent, folder.name, w["week"])
            trel = self.rel(target)
            for f in w["files"]:
                try:
                    created += self._do_move(f["rel"], f"{trel}/{f['name']}")
                    moved.append({"from": f["rel"], "to": f"{trel}/{f['name']}", "dir": False})
                except (LayoutError, OSError) as e:
                    skipped.append({"rel": f["rel"], "reason": str(e)})
        # files Moodle will still give you for these weeks go to the same week folders
        with self.x.conn:
            for r in self.x.files.all():
                wk = week_for(r.section_no, r.sec_name, r.filename, r.moodle_name, lead=True)[0]
                if r.status == "pending" and parent_of(r.rel_path) == plan["folder"] and wk > 0:
                    t = self.rel(week_dir(folder.parent, folder.name, wk))
                    self.retarget([r.key], t)
        return {"plan": plan, "moved": moved, "skipped": skipped, "created": sorted(set(created))}

    # ------------------------------------------------------------------ where Moodle files go
    def retarget(self, keys, dir_rel):
        """Choose the folder a not-yet-downloaded Moodle file will be saved in. Survives later syncs."""
        dst = self._norm(dir_rel)
        for part in (dst.split("/") if dst else []):
            clean_name(part)
        dp = self.abs(dst, must_exist=False)
        if dp.exists() and not dp.is_dir():
            raise LayoutError("the destination is a file")
        x, n = self.x, 0
        with x.conn:
            for k in keys:
                r = x.files.get(k)
                if not r or r.status != "pending":
                    continue
                e = x.pending.get(k)
                name = e.dest.name if e is not None else Path(r.rel_path).name
                x.targets.set(k, dst)
                r.rel_path = f"{dst}/{name}" if dst else name
                x.files.put(r)
                if e is not None and e.kind == "NEW":
                    e.dest = self.root / r.rel_path
                n += 1
        return n

    def allocation(self):
        """Every Moodle file with where it is (or will be) and what state it is in, for the Files page."""
        x, root = self.x, self.root
        courses = {c["id"]: c for c in x.courses.tracked()}
        targets, reviewed = x.targets.all(), x.reviewed.refs("file")
        dirs, files = {}, []

        def is_dir(rel):
            if rel not in dirs:
                dirs[rel] = (root / rel).is_dir() if rel else True
            return dirs[rel]

        for r in x.files.all():
            c = courses.get(r.course_id)
            if c is None:
                continue
            exists = bool(r.rel_path) and (root / r.rel_path).exists()
            if r.status == "pending":
                st = "new"
            elif r.status == "ignored":
                st = "ignored"
            elif not exists:
                st = "missing"
            elif r.has_update:
                st = "update"
            else:
                st = "downloaded"
            folder = parent_of(r.rel_path)
            files.append({"key": r.key, "course": r.course_id, "name": r.filename or Path(r.rel_path).name, "status": st,
                          "rel": r.rel_path, "folder": folder, "folder_exists": is_dir(folder), "section": r.section_no,
                          "sec": r.sec_name, "activity": r.moodle_name, "size": r.size, "adopted": r.adopted,
                          "first_seen": r.first_seen, "chosen": r.key in targets, "reviewed": r.key in reviewed})
        return {"courses": [{"id": c["id"], "short": c["shortname"], "name": c["fullname"], "folder": c["folder_rel"]}
                            for c in courses.values()],
                "files": files, "now": x.ts()}
