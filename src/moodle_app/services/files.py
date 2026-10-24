"""File tracking: compare what Moodle lists with what you have, download what you pick, keep the record in the database.

Rules preserved from the first version (see docs/ARCHITECTURE.md, "Behaviour that must not change"):
only ticked files download; every Moodle file is recorded; copies you already have are matched, not re-downloaded;
moved files are followed; week folders form at a threshold and are reused; old versions are archived, never overwritten.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..db.repos import FileRec
from ..domain.classify import classify
from ..domain.duplicates import VERSIONS_DIR, build_index, find_duplicate, find_moved
from ..domain.util import rel_posix, safe_name, sha256_file
from ..domain.weeks import week_dir, week_for
from ..moodle.fetch import FileItem


@dataclass
class Pending:
    item: FileItem
    kind: str                        # NEW | UPD
    prev: Optional[FileRec]
    dest: Path                       # absolute
    folder: Path                     # module folder (absolute)
    index: dict
    cat: Optional[str] = None
    first_seen: int = 0
    fixed: bool = False              # you chose the folder: week grouping leaves it alone

    @property
    def key(self):
        return self.item.key

    @property
    def week(self):
        it = self.item
        return week_for(it.section_no, it.section_name, it.filename, it.activity_name, lead=True)[0]


@dataclass
class Reconciled:
    pending: list = field(default_factory=list)
    adopted: int = 0
    unchanged: int = 0
    moved: int = 0


def week_counts(files, folder: Path, root: Path, cats):
    """(category, section) -> [keys] of files WE downloaded into <folder>/<category>/... (adopted files are never counted or moved)."""
    out = {}
    for rec in files.values():
        if rec.adopted or rec.status != "downloaded":
            continue
        try:
            parts = (root / rec.rel_path).relative_to(folder).parts
        except ValueError:
            continue
        if len(parts) < 2 or parts[0] not in cats or VERSIONS_DIR in parts:
            continue
        wk = week_for(rec.section_no, rec.sec_name, rec.filename, rec.moodle_name, lead=True)[0]
        if wk:
            out.setdefault((parts[0], wk), []).append(rec.key)
    return out


class FileService:
    def __init__(self, ctx):
        self.x = ctx

    # ---------------------------------------------------------------- reconcile (no downloads)
    def reconcile(self, course_row, items) -> Reconciled:
        x, root = self.x, self.x.root
        folder = root / course_row["folder_rel"]
        cid = course_row["id"]
        cats, rule = x.settings.categories, x.settings.week_folders
        now = x.ts()
        recs = {r.key: r for r in x.files.by_course(cid)}
        chosen = x.targets.all()
        index = build_index(folder)
        res = Reconciled()
        res.moved = self._relocate(recs, index, root)
        claimed = {str(root / r.rel_path) for r in recs.values()
                   if r.status == "downloaded" and r.rel_path and (root / r.rel_path).exists()}
        entries, seen = [], set()
        for it in items:
            seen.add(it.key)
            x.urls[it.key] = it.fileurl
            fname = safe_name(it.filename)
            sig = (it.timemodified, it.size)
            prev = recs.get(it.key)
            status = prev.status if prev else None
            if prev is None or status in ("ignored", "pending"):
                hit = find_duplicate(index, claimed, fname, it.size)
                if hit:                                         # already in this module folder: downloaded, not new/ignored
                    claimed.add(str(hit))
                    rec = FileRec(it.key, cid, "downloaded", rel_posix(hit, root), it.cmid, it.section_no, it.section_name,
                                  it.activity_name, it.filename, True, it.size, sig[0], sig[1], None, None,
                                  prev.first_seen if prev else now, now)
                    x.files.put(rec)
                    recs[it.key] = rec
                    res.adopted += 1
                    continue
            if status == "ignored":
                prev.last_seen = now
                x.files.put(prev)
                continue
            have = prev is not None and status != "pending"
            if have and prev.sig == sig:
                res.unchanged += 1
                prev.upd_modified = prev.upd_size = None
                prev.last_seen = now
                if not prev.section_no:
                    prev.section_no = it.section_no
                x.files.put(prev)
                continue
            if have:
                dest, kind, cat = root / prev.rel_path, "UPD", None
            else:
                cat = classify(cats, it.filename, it.activity_name, it.section_name)
                target = folder if cat == "." else folder / cat
                dest, kind = target / fname, "NEW"
            fixed = kind == "NEW" and it.key in chosen
            if fixed:
                dest = (root / chosen[it.key] / fname) if chosen[it.key] else (root / fname)
            entries.append(Pending(it, kind, prev if kind == "UPD" else None, dest, folder, index, cat,
                                   prev.first_seen if prev else now, fixed))
        # week grouping: >= threshold items of one category from one week -> '<category>/Week NN/'
        if rule["threshold"] > 0:
            counts = week_counts(recs, folder, root, rule["categories"])
            pend = {}
            for e in entries:
                if e.kind == "NEW" and not e.fixed and e.cat in rule["categories"] and e.week:
                    pend.setdefault((e.cat, e.week), []).append(e)
            for (cat, secn), es in pend.items():
                wd = week_dir(folder, cat, secn)
                if wd.is_dir() or len(counts.get((cat, secn), [])) + len(es) >= rule["threshold"]:
                    for e in es:
                        e.dest = wd / e.dest.name
        # record everything Moodle has
        for e in entries:
            it = e.item
            if e.kind == "NEW":
                known = recs.get(it.key)
                rec = FileRec(it.key, cid, "pending", rel_posix(e.dest, root), it.cmid, it.section_no, it.section_name,
                              it.activity_name, it.filename, False, it.size, it.timemodified, it.size, None, None, e.first_seen, now)
                x.files.put(rec)
                recs[it.key] = rec
                if known is None:
                    x.change("new_file", cid, it.key, {"name": it.filename, "to": rec.rel_path})
            else:
                e.prev.upd_modified, e.prev.upd_size, e.prev.last_seen = it.timemodified, it.size, now
                x.files.put(e.prev)
                x.change("updated_file", cid, it.key, {"name": it.filename})
        for k, r in list(recs.items()):                          # removed from Moodle before you downloaded it
            if r.status == "pending" and k not in seen:
                x.files.delete(k)
                x.targets.delete([k])
                del recs[k]
        res.pending = entries
        for e in entries:
            x.pending[e.key] = e
        return res

    def _relocate(self, recs, index, root) -> int:
        """Follow files you moved: a tracked file gone from its recorded path but present elsewhere in the module folder."""
        claimed = {str(root / r.rel_path) for r in recs.values()
                   if r.status == "downloaded" and r.rel_path and (root / r.rel_path).exists()}
        n = 0
        for r in recs.values():
            if r.status != "downloaded" or not r.rel_path:
                continue
            old = root / r.rel_path
            if old.exists():
                continue
            size = r.size or r.sig_size
            hit = find_moved(index, claimed, old, size)
            if hit:
                self.x.log(f"  = followed move  {old.name}  ->  {hit.parent.name}/{hit.name}")
                r.rel_path = rel_posix(hit, root)
                self.x.files.put(r)
                self.x.change("moved", r.course_id, r.key, {"to": r.rel_path})
                claimed.add(str(hit))
                n += 1
        return n

    # ---------------------------------------------------------------- regroup
    def regroup(self, dry_run=False) -> int:
        x, root = self.x, self.x.root
        rule = x.settings.week_folders
        if rule["threshold"] <= 0:
            return 0
        moved = 0
        for c in x.courses.tracked():
            folder = root / c["folder_rel"]
            recs = {r.key: r for r in x.files.by_course(c["id"])}
            for (cat, secn), keys in sorted(week_counts(recs, folder, root, rule["categories"]).items()):
                if secn == 0 or len(keys) < rule["threshold"]:
                    continue
                wdir = week_dir(folder, cat, secn)
                for k in keys:
                    rec = recs[k]
                    p = root / rec.rel_path
                    if p.parent != folder / cat or not p.exists():     # only flat, still-present files
                        continue
                    dest, n = wdir / p.name, 0
                    while dest.exists():
                        n += 1
                        dest = wdir / f"{n}-{p.name}"
                    x.log(f"  ~ {'would group' if dry_run else 'grouped'}  {p.relative_to(folder)}  ->  {dest.relative_to(folder)}")
                    if not dry_run:
                        wdir.mkdir(parents=True, exist_ok=True)
                        p.replace(dest)
                        rec.rel_path = rel_posix(dest, root)
                        x.files.put(rec)
                        x.change("moved", rec.course_id, rec.key, {"to": rec.rel_path})
                    moved += 1
        if moved and not dry_run:
            x.conn.commit()
        return moved

    # ---------------------------------------------------------------- download
    def download(self, entries, moodle=None) -> dict:
        x, root = self.x, self.x.root
        m = moodle or x.moodle
        counts = {"new": 0, "upd": 0, "same": 0, "failed": 0}
        for e in entries:
            it, prev, dest, folder, index = e.item, e.prev, e.dest, e.folder, e.index
            if not prev:                              # pick a free name (different file, same name -> week prefix)
                fname, n = dest.name, 0
                while dest.exists():
                    n += 1
                    pre = f"{it.section_no:02d} " if n == 1 else f"{it.section_no:02d}-{n} "
                    dest = dest.parent / (pre + fname)
            try:
                rel = dest.relative_to(folder)
            except ValueError:                        # you chose a folder outside this module's folder
                rel = dest.relative_to(root)
            tmp = folder / ".moodle_download.tmp"
            try:
                m.download(it.fileurl, tmp)
            except Exception as ex:
                x.log(f"  ! {rel}: download failed ({ex})")
                counts["failed"] += 1
                continue
            now = x.ts()
            base = dict(course_id=it.course_id, cmid=it.cmid, section_no=it.section_no, sec_name=it.section_name,
                        moodle_name=it.activity_name, filename=it.filename, size=it.size, sig_modified=it.timemodified,
                        sig_size=it.size, upd_modified=None, upd_size=None, first_seen=e.first_seen or now, last_seen=now)
            if not prev:                              # confirm duplicates by content (catches small files)
                got = tmp.stat().st_size
                dup = next((p for p in index.get(got, []) if sha256_file(p) == sha256_file(tmp)), None)
                if dup:
                    tmp.unlink()
                    x.log(f"  = {rel}: identical to {rel_posix(dup, root)}, not kept (marked downloaded)")
                    with x.conn:
                        x.files.put(FileRec(it.key, status="downloaded", rel_path=rel_posix(dup, root), adopted=True, **base))
                        x.targets.delete([it.key])
                    counts["same"] += 1
                    continue
            if dest.exists():                         # keep the previous version rather than overwrite silently
                vdir = dest.parent / VERSIONS_DIR
                vdir.mkdir(exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(dest.stat().st_mtime))
                dest.replace(vdir / f"{dest.stem}.{stamp}{dest.suffix}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp.replace(dest)
            if it.timemodified:
                os.utime(dest, (it.timemodified, it.timemodified))
            with x.conn:                              # saved file by file: an interrupted run loses nothing
                x.files.put(FileRec(it.key, status="downloaded", rel_path=rel_posix(dest, root),
                                    adopted=bool(prev and prev.adopted), **base))
                x.change("downloaded", it.course_id, it.key, {"to": rel_posix(dest, root), "update": bool(prev)})
                x.targets.delete([it.key])
            index.setdefault(dest.stat().st_size, []).append(dest)
            x.pending.pop(it.key, None)
            counts["upd" if prev else "new"] += 1
            x.log(f"  + {'updated' if prev else 'saved'}  {rel}")
        return counts

    # ---------------------------------------------------------------- decisions
    def ignore(self, keys) -> int:
        n = 0
        with self.x.conn:
            for k in keys:
                r = self.x.files.get(k)
                if r and r.status == "pending":
                    r.status = "ignored"
                    self.x.files.put(r)
                    self.x.pending.pop(k, None)
                    self.x.change("ignored", r.course_id, k)
                    n += 1
        return n

    def close_missing(self, keys) -> int:
        """You deleted these yourself: they stop showing as missing and are not offered again (recorded as ignored)."""
        n = 0
        with self.x.conn:
            for k in keys:
                r = self.x.files.get(k)
                if r and r.status == "downloaded" and r.rel_path and not (self.x.root / r.rel_path).exists():
                    r.status, r.upd_modified, r.upd_size = "ignored", None, None
                    self.x.files.put(r)
                    self.x.change("closed", r.course_id, k)
                    n += 1
        return n

    def unignore(self, keys) -> int:
        n = 0
        with self.x.conn:
            for k in keys:
                r = self.x.files.get(k)
                if r and r.status == "ignored":
                    self.x.files.delete(k)
                    n += 1
        return n

    def redownload(self, key):
        with self.x.conn:
            r = self.x.files.get(key)
            if not r or r.status in ("pending", "ignored"):
                raise ValueError("unknown file")
            self.x.files.delete(key)
