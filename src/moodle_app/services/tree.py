"""The folder tree shown by the GUI: your real folders overlaid with what the database knows (new, ignored, missing, ...)."""
from __future__ import annotations

import os
from pathlib import Path


def build_tree(ctx, scanned_at=None, scan_error=None):
    x = ctx
    root, hide = x.root, set(x.settings.tree_hide)
    recs = x.files.all()
    real = {}
    for r in recs:
        if r.status == "downloaded" and r.rel_path:
            real.setdefault(r.rel_path, r)
    generated = set(x.notes_manifest.all())
    reviewed = x.reviewed.refs("file")
    top = {"name": root.name, "rel": "", "type": "dir", "virtual": False, "kids": {}}

    def ensure(parts, virtual=False):
        node = top
        for i, part in enumerate(parts):
            kid = node["kids"].get(part)
            if kid is None:
                kid = {"name": part, "rel": "/".join(parts[: i + 1]), "type": "dir", "virtual": virtual, "kids": {}}
                node["kids"][part] = kid
            node = kid
        return node

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in hide)
        rel = Path(dirpath).relative_to(root)
        node = ensure(rel.parts)
        for fn in sorted(filenames):
            if fn.startswith(".") or fn in hide:
                continue
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            parts = rel.parts + (fn,)
            relp = "/".join(parts)
            n = {"name": fn, "rel": relp, "type": "file", "size": st.st_size,
                 "ts": getattr(st, "st_birthtime", st.st_mtime), "mtime": st.st_mtime}
            hit = real.get(relp)
            if hit:
                n.update(status="update" if hit.has_update else ("adopted" if hit.adopted else "downloaded"),
                         key=hit.key, moodle=hit.moodle_name, week=hit.sec_name, reviewed=hit.key in reviewed)
            elif "_old_versions" in parts:
                n["status"] = "archived"
            elif "formatted txt" in parts or relp in generated:
                n["status"] = "generated"
            else:
                n["status"] = "local"
            node["kids"][fn] = n
    for r in recs:                                    # records for Moodle files that are not (yet) on disk
        if r.status == "pending":
            status = "new"
        elif r.status == "ignored":
            status = "ignored"
        elif r.rel_path and not (root / r.rel_path).exists():
            status = "missing"
        else:
            continue
        parts = tuple(r.rel_path.split("/"))
        parent = ensure(parts[:-1], virtual=True)
        name = parts[-1]
        key = name if name not in parent["kids"] else f"{name}\0{status}"
        parent["kids"][key] = {"name": name, "rel": r.rel_path, "type": "file", "status": status, "key": r.key,
                               "size": r.size, "moodle": r.moodle_name, "week": r.sec_name, "first_seen": r.first_seen}

    totals = {"new": 0, "update": 0, "local": 0, "ignored": 0, "missing": 0, "downloaded": 0}

    def finish(node):
        kids = list(node["kids"].values()) if node["type"] == "dir" else []
        for kid in kids:
            if kid["type"] == "dir":
                finish(kid)
            else:
                t = {"adopted": "downloaded"}.get(kid["status"], kid["status"])
                if t in totals:
                    totals[t] += 1
        node["kids"] = sorted(kids, key=lambda k: (k["type"] != "dir", k["name"].lower()))

    finish(top)
    return {"root": top, "totals": totals, "scanned_at": scanned_at, "scan_error": scan_error}
