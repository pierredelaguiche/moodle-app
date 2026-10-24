"""Import the first version (moodle-tool): its config, token and .moodle_sync_state.json (your Ignored/Missing decisions)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..config import save_token, write_private
from ..db.repos import FileRec


def import_legacy(ctx, legacy_dir):
    """Returns a summary dict. Existing records in the new database are never overwritten."""
    legacy = Path(legacy_dir).expanduser()
    s, log = ctx.settings, ctx.log
    out = {"config": "kept", "token": "kept", "files": 0, "skipped_existing": 0, "outside_root": 0}
    cfg_old = legacy / "moodle_sync.config.json"
    if not cfg_old.exists() and not (legacy / ".moodle_sync_state.json").exists():
        raise ValueError(f"{legacy} does not look like a moodle-tool folder (no config or state file).")
    if cfg_old.exists() and not s.config_path.exists():
        old = json.loads(cfg_old.read_text(encoding="utf-8"))
        keep = {k: old[k] for k in ("site_url", "dest_root", "exclude_course_ids", "keywords", "week_folders", "tree_hide") if k in old}
        write_private(s.config_path, json.dumps(keep, indent=2) + "\n")
        out["config"] = "imported"
        from .. import config
        fresh = config.load(s.home)
        s.site_url, s.dest_root, s.exclude_course_ids = fresh.site_url, fresh.dest_root, fresh.exclude_course_ids
        s.keywords, s.week_folders = fresh.keywords, fresh.week_folders
    tok = legacy / ".moodle_token"
    if tok.exists() and not s.token_path.exists():
        save_token(s, tok.read_text())
        out["token"] = "imported"
    st_path = legacy / ".moodle_sync_state.json"
    if not st_path.exists():
        return out
    state = json.loads(st_path.read_text(encoding="utf-8"))
    root = s.dest_root.resolve() if s.dest_root else None
    now = ctx.ts()
    with ctx.conn:
        for key, v in state.items():
            try:
                cid_s, cmid_s, rest = key.split(":", 2)
                cid, cmid = int(cid_s), int(cmid_s)
            except ValueError:
                continue
            if ctx.files.get(key):
                out["skipped_existing"] += 1
                continue
            path = Path(v.get("path", ""))
            try:
                rel = path.resolve().relative_to(root).as_posix() if root else ""
            except ValueError:
                out["outside_root"] += 1
                continue
            if ctx.courses.get(cid) is None:
                folder = rel.split("/")[0] if rel else None
                ctx.courses.upsert({"id": cid}, folder, bool(folder), now)       # placeholder until the first sync fills it in
            sig = v.get("sig") or [0, 0]
            upd = v.get("update_sig")
            ctx.files.put(FileRec(
                key=key, course_id=cid, status=v.get("status") or "downloaded", rel_path=rel, cmid=cmid,
                section_no=v.get("section", 0) or 0, sec_name=v.get("sec_name", ""), moodle_name=v.get("moodle_name", ""),
                filename=rest.rsplit("/", 1)[-1], adopted=bool(v.get("adopted")), size=v.get("size") or sig[1] or 0,
                sig_modified=sig[0], sig_size=sig[1], upd_modified=upd[0] if upd else None, upd_size=upd[1] if upd else None,
                first_seen=v.get("first_seen") or now, last_seen=now))
            out["files"] += 1
    run = ctx.history.start("import", now)
    with ctx.conn:
        ctx.history.finish(run, ctx.ts(), True, None, out)
    log(f"Imported {out['files']} file records ({out['skipped_existing']} already present, {out['outside_root']} outside dest_root skipped).")
    return out
