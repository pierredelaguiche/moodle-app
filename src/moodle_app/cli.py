"""Command line. Thin: parse arguments, build a Ctx, call a service."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__, config, db
from .config import ConfigError
from .moodle import auth
from .moodle.client import Moodle, MoodleError, NetworkError
from .services.context import Ctx

TEMPLATE = {
    "site_url": "",
    "dest_root": "",
    "exclude_course_ids": [],
    "keywords": {},
    "week_folders": {"threshold": 3, "categories": ["Workshops", "Lectures"]},
    "sync": {"files": True, "deadlines": True, "announcements": True, "submission_status_days": 30, "min_interval_minutes": 15},
    "llm": {"provider": "auto", "model": "haiku", "key_file": ".llm_key"},
    "notes": {"confirm_tokens": 30000},
    "open_on_launch": "tab",
    "port": 8765,
}


def build_ctx(args, need_moodle=True, need_root=True):
    s = config.load(args.home)
    s.home.mkdir(parents=True, exist_ok=True)
    conn = db.connect(s.db_path)
    db.migrate(conn)
    m = None
    if need_moodle:
        m = Moodle(s.require_site(), config.read_token(s))
    if need_root:
        s.require_root()
    return Ctx(s, conn, m)


def fmt_ts(ts):
    return time.strftime("%a %d %b %H:%M", time.localtime(ts)) if ts else ""


# ---------------------------------------------------------------- commands
def cmd_init(args):
    s = config.load(args.home)
    s.home.mkdir(parents=True, exist_ok=True)
    if not s.config_path.exists():
        config.write_private(s.config_path, json.dumps(TEMPLATE, indent=2) + "\n")
        print(f"Wrote {s.config_path}. Set \"dest_root\" to the folder that holds one folder per module.")
    else:
        print(f"Config already exists: {s.config_path}")
    conn = db.connect(s.db_path)
    print(f"Database: {s.db_path} (schema v{db.migrate(conn)})")
    print("Next: `moodle-app token`, then `moodle-app courses`.")


def cmd_token(args):
    s = config.load(args.home)
    base = s.require_site()
    url, passport = auth.begin(base)
    print("1. Open this link in your browser (logged in to Moodle):\n\n   " + url + "\n")
    print("2. Moodle redirects to an address starting with  moodlemobile://token=...")
    print("   The browser can't open it, so copy that address instead:")
    print("   - Page shows an 'Open in app'/'Go' link -> right-click it -> Copy link address; or")
    print("   - DevTools (Cmd+Opt+I) > Network tab (tick 'Preserve log') BEFORE opening the link,")
    print("     click the 'launch.php' request > Response Headers > 'location'.\n")
    try:
        tok, ok = auth.parse(base, passport, input("3. Paste it here: "))
    except auth.HandshakeError as e:
        sys.exit(str(e))
    if not ok:
        print("Warning: handshake check did not match (link may be from an earlier attempt). Trying the token anyway.")
    config.save_token(s, tok)
    print(f"Saved the token to {s.token_path} (mode 0600). Treat it like a password.")


def cmd_courses(args):
    from .services import sync
    ctx = build_ctx(args)
    sync.sync_courses(ctx, ctx.moodle)
    ctx.conn.commit()
    for c in ctx.courses.all():
        print(f"[{c['id']}] {c['fullname']}  ->  {c['folder_rel'] or '(no matching folder)'}")


def cmd_sync(args):
    from .services import sync
    ctx = build_ctx(args)
    if args.if_due and sync.too_soon(ctx):
        print("Last sync was recent; skipping (--if-due).")
        return
    what = args.only.split(",") if args.only else None
    s = sync.run_sync(ctx, what=what, only_course=args.course)
    if s["warnings"]:
        print(f"{len(s['warnings'])} warning(s) above.")


def cmd_pending(args):
    from .services import sync
    from .domain.util import human_size
    ctx = build_ctx(args)
    sync.run_sync(ctx, what={"files"}, only_course=args.course)
    cur = None
    for e in sorted(ctx.pending.values(), key=lambda e: (str(e.folder), str(e.dest))):
        if e.folder != cur:
            cur = e.folder
            print(f"\n== {cur.name}")
        print(f"   {e.kind} {e.dest.relative_to(e.folder)}   {human_size(e.item.size)}  · {e.item.section_name} / {e.item.activity_name}")
    if not ctx.pending:
        print("Nothing pending.")


def cmd_download(args):
    from .services import sync
    from .services.files import FileService
    ctx = build_ctx(args)
    if not args.all:
        sys.exit("Choose what to download: --all (everything pending). For picking files, use `moodle-app serve`.")
    sync.run_sync(ctx, what={"files"}, only_course=args.course)
    chosen = list(ctx.pending.values())
    if not chosen:
        return print("Nothing to download.")
    svc = FileService(ctx)
    print(f"Downloading {len(chosen)} file(s)...")
    c = svc.download(chosen)
    with ctx.conn:
        svc.regroup()
    print(f"Done: {c['new']} new, {c['upd']} updated, {c['same']} identical to existing, {c['failed']} failed.")


def cmd_deadlines(args):
    ctx = build_ctx(args, need_moodle=False, need_root=False)
    now = ctx.ts()
    rows = ctx.deadlines.upcoming(now - (365 * 86400 if args.all else 86400), include_hidden=args.all, include_gone=args.all)
    n = 0
    for r in rows:
        if not args.all and (r["done"] or r["status"] != "open"):
            continue
        flag = "OVERDUE " if r["status"] == "open" and r["due_ts"] < now else ""
        print(f"{fmt_ts(r['due_ts']):<17} [{r['course_short']}] {r['title']}  {flag}{r['status'] if r['status'] != 'open' else ''}")
        n += 1
    if not n:
        print("No open deadlines. Run `moodle-app sync` first.")


def cmd_announcements(args):
    from .web.server import html_to_text
    ctx = build_ctx(args, need_moodle=False, need_root=False)
    for a in ctx.announcements.recent(args.limit, unread_only=args.unread):
        print(f"{fmt_ts(a['created'])}  [{a['course_short']}] {a['subject']}  ({a['author']})")
        if args.full:
            print("   " + html_to_text(a["message"]).replace("\n", "\n   ") + "\n")


def cmd_digest(args):
    from .services import digest
    x = build_ctx(args, need_moodle=False, need_root=False)
    d = digest.build(x)
    print(digest.render_text(d))
    if args.seen:
        digest.mark_seen(x)


def cmd_history(args):
    ctx = build_ctx(args, need_moodle=False, need_root=False)
    for r in ctx.history.recent_changes(args.limit):
        d = json.loads(r["detail"]) if r["detail"] else {}
        print(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(r['ts']))}  {r['kind']:<17} {d.get('name') or d.get('title') or d.get('subject') or r['ref']}")


def cmd_ics(args):
    from .services import ics
    ctx = build_ctx(args, need_moodle=False, need_root=False)
    text = ics.build_ics(ctx.deadlines.all_for_ics(), events=ctx.events.all())
    if args.out:
        Path(args.out).expanduser().write_text(text, encoding="utf-8", newline="")
        print(f"Wrote {args.out}. Import it into your calendar; re-importing updates events in place.")
    else:
        sys.stdout.write(text)


def cmd_notes(args):
    from .services.notes import run_notes
    ctx = build_ctx(args, need_moodle=False)
    sys.exit(run_notes(ctx, args.module, what=args.what, week=args.week, yes=args.yes, force=args.force, dry_run=args.dry_run))


def cmd_audit(args):
    from .services import audit
    from .services.layout import Layout
    ctx = build_ctx(args, need_moodle=False)
    rows = audit.audit(ctx)
    for r in rows:
        print(f"{r['path']}\n    section {r['section']} '{r['section_name']}': " + "; ".join(r["issues"]) + ("   [auto-placed]" if r.get("auto") else ""))
    print(f"{len(rows)} file(s) look misplaced." if rows else "No misplaced files found.")
    if args.fix and rows:
        moved = audit.fix(ctx, rows, Layout(ctx))
        print(f"Moved {len(moved)} auto-placed file(s) into the right week (the rest are yours to judge).")


def cmd_llm_check(args):
    from .llm.providers import LLMError, diagnose, make_provider
    ctx = build_ctx(args, need_moodle=False, need_root=False)
    print(f"Config: provider={ctx.settings.llm.get('provider', 'auto')}, model={ctx.settings.llm.get('model', 'haiku')}")
    print("\n".join(diagnose(ctx.settings.llm, ctx.settings.home)))
    from .services import usage
    try:
        p = make_provider(ctx.settings.llm, ctx.settings.home, limits=usage.load_limits(ctx))
        print(f"Using {p.destination}")
        r = p.complete("Answer with one word.", "Say: ready", 50, **({"check": False} if p.name == "claude_cli" else {}))
    except LLMError as e:
        sys.exit(f"LLM not usable: {e}")
    usage.save_limits(ctx, p.limits)
    if p.limits:
        print(usage.line(ctx))
    if p.limit_problem():
        print("Note: " + p.limit_problem())
    print(f"Answer: {r.text.strip()[:60]}   (tokens in {r.input_tokens}, out {r.output_tokens})")


def cmd_serve(args):
    from .web import server
    ctx = build_ctx(args, need_moodle=False, need_root=False)          # a first run has neither yet: the Help page sets them up
    config.ensure_defaults(ctx.settings)
    if ctx.settings.site_url:
        try:
            ctx.moodle = Moodle(ctx.settings.site_url, config.read_token(ctx.settings))
        except config.ConfigError:
            pass
    server.run(ctx, port=args.port, open_browser=not args.no_browser, how=args.open)


def cmd_probe(args):
    from .moodle import probe
    from .moodle.fetch import fetch_courses, fetch_site
    ctx = build_ctx(args, need_root=False)
    site = fetch_site(ctx.moodle)
    exclude = ctx.settings.exclude_course_ids
    courses = [c for c in fetch_courses(ctx.moodle, site["userid"]) if str(c["id"]) not in exclude]
    probe.run(ctx.moodle, courses, ctx.settings.home / "probe_report.json")


def cmd_status(args):
    ctx = build_ctx(args, need_moodle=False, need_root=False)
    print(f"moodle-app {__version__}   data: {ctx.settings.home}   schema v{db.schema_version(ctx.conn)}")
    last = ctx.history.last_run("sync")
    print("last sync: " + (time.strftime("%Y-%m-%d %H:%M", time.localtime(last["finished"])) if last else "never"))
    from .services import usage
    print(usage.line(ctx))
    print(f"files: {ctx.files.counts()}   courses tracked: {len(ctx.courses.tracked())}   "
          f"site: {ctx.settings_repo.get('site', {}).get('release', 'unknown')}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="moodle-app", description="Local Moodle companion: files, deadlines, announcements, notes.")
    ap.add_argument("--home", help="data directory (default: MOODLE_APP_HOME or the OS default)")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        return p

    add("init", cmd_init, "create the data directory, config template and database")
    add("token", cmd_token, "get a Moodle token through the browser login")
    add("courses", cmd_courses, "list your courses and which local folder each maps to")
    p = add("sync", cmd_sync, "check Moodle: files, deadlines, announcements")
    p.add_argument("--only", help="comma list of: files,deadlines,announcements")
    p.add_argument("--course", help="only courses whose name contains this")
    p.add_argument("--if-due", action="store_true", help="skip when the last sync was within sync.min_interval_minutes")
    p = add("pending", cmd_pending, "check Moodle and list files not downloaded yet")
    p.add_argument("--course")
    p = add("download", cmd_download, "download pending files")
    p.add_argument("--all", action="store_true")
    p.add_argument("--course")
    p = add("deadlines", cmd_deadlines, "show open deadlines (from the last sync)")
    p.add_argument("--all", action="store_true", help="include done, hidden, removed and past ones")
    p = add("announcements", cmd_announcements, "show recent announcements")
    p.add_argument("--unread", action="store_true")
    p.add_argument("--full", action="store_true", help="print the message text too")
    p.add_argument("--limit", type=int, default=20)
    p = add("digest", cmd_digest, "what is due, what changed and where you are behind")
    p.add_argument("--seen", action="store_true", help="remember that you have seen it (next digest shows what is new since now)")
    p = add("history", cmd_history, "what changed in recent runs")
    p.add_argument("--limit", type=int, default=40)
    p = add("ics", cmd_ics, "write the deadlines as an .ics calendar file")
    p.add_argument("--out", help="file to write (default: standard output)")
    p = add("notes", cmd_notes, "generate lecture notes, workshop summaries/files and a study guide with an LLM (manual, token-guarded)")
    p.add_argument("module", help="module folder name or unique prefix, e.g. CSCI.323")
    p.add_argument("--what", default="all", help="lectures, summaries, code, workshops, coursework, guide, all (comma-separated)")
    p.add_argument("--week", type=int)
    p.add_argument("--yes", action="store_true", help="skip the confirmation question")
    p.add_argument("--force", action="store_true", help="regenerate and replace edited files (keeps a .bak)")
    p.add_argument("--dry-run", action="store_true", help="print the plan only; send and write nothing")
    p = add("audit", cmd_audit, "list files that sit in the wrong week/category folder; --fix moves the auto-placed ones")
    p.add_argument("--fix", action="store_true")
    add("llm-check", cmd_llm_check, "test the LLM connection used by notes (Claude login or API key)")
    p = add("serve", cmd_serve, "open the local web interface")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--no-browser", action="store_true", help="same as --open none")
    p.add_argument("--open", choices=["tab", "window", "none"], help="how to open the page this time (default: the open_on_launch setting, 'tab')")
    add("probe", cmd_probe, "read-only check of what your Moodle token can do (writes probe_report.json)")
    add("status", cmd_status, "show data directory, schema version, last sync")

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except ConfigError as e:
        sys.exit(f"Config problem: {e}")
    except MoodleError as e:
        sys.exit(f"Moodle said no: {e}" + ("  (token expired or wrong? run `moodle-app token`)" if e.code == "invalidtoken" else ""))
    except NetworkError as e:
        sys.exit(f"Could not reach Moodle: {e}")
    except KeyboardInterrupt:
        sys.exit("\nInterrupted.")


if __name__ == "__main__":
    main()
