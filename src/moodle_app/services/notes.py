"""`notes`: manual, token-guarded study-material generation for one module (lectures, workshop summaries, workshop .py files,
study guide). Planning and generation live in moodle_app.notes.planner; this is the use-case around them."""
from __future__ import annotations

import sys
from pathlib import Path

from ..llm.providers import LLMError, LLMLimit, make_provider
from ..notes import planner
from . import usage as usage_service


def resolve_module(root, name):
    """A module folder from an absolute path, a name under dest_root, or a unique prefix such as 'CSCI.323'."""
    p = Path(name).expanduser()
    if p.is_absolute():
        if p.is_dir():
            return p
        raise ValueError(f"Not a folder: {p}")
    if (root / name).is_dir():
        return root / name
    from ..domain.years import module_dirs
    hits = [root / rel for rel in module_dirs(root) if rel.rsplit("/", 1)[-1].lower().startswith(name.lower()) or rel.lower().startswith(name.lower())]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ValueError(f"No module folder starting with '{name}' under {root}")
    raise ValueError(f"'{name}' matches several folders: {', '.join(sorted(h.name for h in hits))}")


def confirm(question, input_fn=input, interactive=None):
    interactive = sys.stdin.isatty() if interactive is None else interactive
    if not interactive:
        return False
    return input_fn(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def run_notes(ctx, module_name, what="all", week=None, yes=False, force=False, dry_run=False,
              input_fn=input, interactive=None, provider=None, only=None, select=None):
    """Returns a process exit code. Nothing is sent to an LLM before the plan has been printed (and confirmed when large)."""
    log, s = ctx.log, ctx.settings
    try:
        kinds = planner.parse_what(what)
        module = resolve_module(ctx.root, module_name)
    except ValueError as e:
        log(str(e))
        return 2
    if week is not None and "study_guide" in kinds and kinds != {"study_guide"}:
        log("Note: --week limits lectures/workshops; the study guide always covers the whole module and is skipped.")
    try:
        plan = planner.build_plan(module, kinds, week, s.notes, ctx.notes_manifest.all(), force=force, log=log, only=only, select=select)
    except RuntimeError as e:                                   # e.g. pymupdf missing
        log(str(e))
        return 1
    if provider is None:
        try:
            provider = make_provider(s.llm, s.home, cache_dir=s.home / "cache" / "llm", limits=usage_service.load_limits(ctx))
        except LLMError as e:
            if not dry_run:
                log(str(e))
                return 1
            log(f"(no LLM configured yet: {e})")
    log(planner.describe(plan, provider))
    if not plan.runnable():
        return 0
    if dry_run:
        log("\nDry run: nothing sent, nothing written.")
        return 0
    if not dry_run and provider.limit_problem():
        log(f"Stopped: {provider.limit_problem()} Nothing was sent.")
        return 1
    log(usage_service.line(ctx))
    if plan.needs_confirm() and not yes:
        why = "includes the study guide" if any(j.kind == "study_guide" for j in plan.runnable()) \
            else f"is over {plan.cfg['confirm_tokens']:,} input tokens"
        if not confirm(f"\nThis run {why}. Send it?", input_fn, interactive):
            log("Cancelled. Nothing was sent. (Use --yes to skip this question, or --what/--week to make it smaller.)")
            return 1
    run_id = ctx.history.start("notes", ctx.ts())
    ctx.conn.commit()
    try:
        totals = planner.execute(plan, provider, ctx.notes_manifest, log=log, force=force, check=getattr(ctx, "check", None))
    except BaseException as e:                                     # cancelled or crashed: what was written is kept, the run is closed
        with ctx.conn:
            ctx.history.finish(run_id, ctx.ts(), False, str(e) or type(e).__name__.lower(), {})
        raise
    usage_service.save_limits(ctx, provider.limits)
    with ctx.conn:
        ctx.history.finish(run_id, ctx.ts(), not totals["failed"], None, totals)
    log(f"\nDone: {totals['written']} written, {totals['skipped']} skipped, {totals['failed']} failed. "
        f"Tokens used: {totals['in']:,} in, {totals['out']:,} out"
        + (f" ({totals['cached']} request(s) answered from cache)." if totals["cached"] else "."))
    if totals.get("stopped"):
        log(f"Stopped early: {totals['stopped']} Work done so far is kept; run it again after the limit resets.")
    return 1 if totals["failed"] or totals.get("stopped") else 0
