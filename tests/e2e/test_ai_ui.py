"""Browser tests for the AI page and the model table: plan usage is said once (the chip in the header), card intros are
one line, and every send is preceded by a confirm that names the model and says whether the text leaves this computer."""
import json
import sys
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
from test_ui import browser, goto, ui  # noqa: E402,F401  (fixtures/helpers)

MOD = "CSCI.999 Test Module"
FAKE_CLI = [sys.executable, str(Path(__file__).resolve().parent.parent / "fake_cli.py")]


def runs(log):
    return [json.loads(ln) for ln in log.read_text().splitlines()] if log.exists() else []


def fake_claude(ui, tmp_path, five_hour):
    """A `claude` stand-in that reports the 5-hour window at `five_hour` (0..1) and answers 'ok'."""
    ev = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour", "overageStatus": "rejected",
          "unifiedWindows": {"five_hour": {"utilization": five_hour, "resetsAt": ui.now + 3600, "status": "allowed"},
                             "seven_day": {"utilization": 0.4, "resetsAt": ui.now + 86400, "status": "allowed"}}}}
    res = {"type": "result", "result": "ok", "is_error": False, "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}
    (tmp_path / "out.txt").write_text(json.dumps(ev) + "\n" + json.dumps(res) + "\n")
    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\ncat >/dev/null\ncat {tmp_path}/out.txt\n")
    fake.chmod(0o755)
    ui.ctx.settings.llm = {"provider": "claude_cli", "model": "haiku", "executable": str(fake)}


def test_usage_is_one_chip_and_a_confirm_mentions_it_only_when_high(ui, tmp_path):
    fake_claude(ui, tmp_path, 0.72)
    (ui.folder / "Lectures").mkdir(exist_ok=True)
    (ui.folder / "Lectures" / "01 Intro.txt").write_text("parsing " * 80)
    page = ui.page
    goto(ui, "ai")
    page.wait_for_selector("#ai-usage")
    assert "5h" not in page.locator("#ai-usage").inner_text()                 # nothing known yet: the app's own count, no plan numbers
    page.locator("button", has_text="Show plan").click()                      # a plan asks Claude where the plan limits stand
    page.wait_for_selector(".ai-plan .ai-job")
    chip = page.locator("#ai-usage")
    assert chip.inner_text() == "Claude · 5h 72% · 7d 40%" and "b-update" in chip.get_attribute("class")
    tip = chip.get_attribute("title")
    assert "Checked" in tip and "Extra usage off" in tip and "tokens sent by this app in 7 days" in tip
    body = page.inner_text("#main")
    assert body.count("72%") == 1 and page.locator("#ai-usage").count() == 1  # the plan repeats nothing
    assert "plan usage" not in body.lower()
    page.locator("button.primary", has_text="Create").click()
    page.wait_for_selector("dialog[open]")
    said = page.locator("dialog[open]").inner_text()
    assert "Model: haiku, sent to Anthropic." in said and said.count("72%") == 1 and "7d" not in said      # only the window above 60%
    page.keyboard.press("Escape")
    assert not (ui.folder / "Notes" / "Lectures").exists()                   # nothing was sent or written without the confirm
    assert [e for e in page.errors if e[0] in ("pageerror", "error")] == [], page.errors


def test_a_confirm_says_nothing_about_usage_when_it_is_low(ui, tmp_path):
    fake_claude(ui, tmp_path, 0.12)
    (ui.folder / "Lectures").mkdir(exist_ok=True)
    (ui.folder / "Lectures" / "01 Intro.txt").write_text("parsing " * 80)
    page = ui.page
    goto(ui, "ai")
    page.locator("button", has_text="Show plan").click()
    page.wait_for_selector(".ai-plan .ai-job")
    assert page.locator("#ai-usage").inner_text() == "Claude · 5h 12% · 7d 40%"       # the plan asked Claude; the chip took the numbers
    assert "b-update" not in page.locator("#ai-usage").get_attribute("class")
    page.locator("button.primary", has_text="Create").click()
    page.wait_for_selector("dialog[open]")
    assert "%" not in page.locator("dialog[open]").inner_text()


@pytest.mark.parametrize("route", ["ai", "settings", "chat", "files"])
def test_intro_paragraphs_take_one_line(ui, route):
    goto(ui, route)
    ui.page.wait_for_timeout(600)
    tall = ui.page.evaluate("""() => [...document.querySelectorAll('#main p.muted, #main p.hint')].filter((p) => p.offsetParent)
        .map((p) => [p.textContent.slice(0, 50), Math.round(p.getBoundingClientRect().height / parseFloat(getComputedStyle(p).lineHeight))])
        .filter(([, lines]) => lines > 1)""")
    assert tall == []


# ---------------------------------------------------------------- a model per task
def local_cli(log, answer="a local answer"):
    return {"provider": "cli", "command": FAKE_CLI + ["{model}"], "model": "qwen3", "local": True, "env": {"FAKE_CLI_LOG": str(log), "FAKE_CLI_ANSWER": answer}}


def test_create_confirm_names_a_local_model_and_says_the_text_stays(ui, tmp_path):
    log = tmp_path / "cli.jsonl"
    ui.ctx.settings.llm = local_cli(log, "# Notes\n\nbody")
    (ui.folder / "Lectures").mkdir(exist_ok=True)
    (ui.folder / "Lectures" / "01 Intro.txt").write_text("parsing " * 80)
    page = ui.page
    goto(ui, "ai")
    page.wait_for_selector("#ai-model")
    assert page.locator("#ai-model").inner_text() == "qwen3, stays on this computer"
    page.locator("button", has_text="Show plan").click()
    page.wait_for_selector(".ai-plan .ai-job")
    page.locator("button.primary", has_text="Create").click()
    page.wait_for_selector("dialog[open]")
    assert "Model: qwen3, stays on this computer." in page.locator("dialog[open]").inner_text()
    assert runs(log) == []                                                    # planning and the confirm sent nothing
    page.locator("dialog[open] button.primary").click()
    page.wait_for_function("() => !document.querySelector('.ai-plan') && document.documentElement.dataset.busy === '0'", timeout=20000)
    assert (ui.folder / "Notes" / "Lectures" / "Week 01.md").exists() and len(runs(log)) == 1


def test_the_study_assistant_and_the_course_chat_confirm_the_model_once(ui, tmp_path):
    log = tmp_path / "cli.jsonl"
    fake_claude(ui, tmp_path, 0.1)
    ui.ctx.settings.llm["tasks"] = {"chat": local_cli(log)}                   # both chats run on the chat task: here a local model
    page = ui.page
    goto(ui, "study")
    page.wait_for_function("() => (document.querySelector('.st-to') || {}).textContent === 'qwen3, stays on this computer'")     # known before any send
    box = page.locator(".st-ask textarea")
    box.fill("What is this?")
    page.locator(".st-ask button.primary").click()
    page.wait_for_selector("dialog[open]")
    said = page.locator("dialog[open]").inner_text()
    assert "Send to qwen3?" in said and "Your text stays on this computer." in said
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    assert runs(log) == [] and page.locator(".st-thread .msg").count() == 0 and box.input_value() == "What is this?"     # cancelled: nothing sent, the message is kept
    page.locator(".st-ask button.primary").click()
    page.locator("dialog[open] button.primary").click()
    page.wait_for_selector(".st-thread .msg.ai >> text=a local answer")
    box.fill("And this?")
    page.locator(".st-ask button.primary").click()
    page.wait_for_function("() => document.querySelectorAll('.st-thread .msg.ai:not(.wait)').length === 2")
    assert page.locator("dialog[open]").count() == 0 and len(runs(log)) == 2  # asked once per model, not per message

    goto(ui, "ai")                                                            # Chat with the course: the same model, already confirmed in this tab
    page.wait_for_selector("#ai-chat textarea")
    page.locator("#ai-chat textarea").fill("What is parsing?")
    page.locator("#ai-chat button.primary").click()
    page.wait_for_selector("#ai-chat .ai-msg.ai >> text=a local answer")
    assert page.locator("dialog[open]").count() == 0 and len(runs(log)) == 3
    assert "Student: What is parsing?" in runs(log)[2]["stdin"] and "And this?" not in runs(log)[2]["stdin"]      # its own conversation
    ui.ctx.settings.llm["tasks"] = {}                                         # the model changes: asked again, and now the text leaves
    page.locator("#ai-chat textarea").fill("And lexing?")
    page.keyboard.press("Control+Enter")
    page.wait_for_selector("dialog[open]")
    said = page.locator("dialog[open]").inner_text()
    assert "Send to haiku?" in said and "Your text is sent to Anthropic." in said
    page.locator("dialog[open] button.primary").click()
    page.wait_for_function("() => document.querySelectorAll('#ai-chat .ai-msg.ai:not(.wait)').length === 2")
    assert [e for e in page.errors if e[0] in ("pageerror", "error")] == [], page.errors


def test_settings_model_table_sets_a_task_and_tests_it(ui, tmp_path):
    import shlex
    log = tmp_path / "cli.jsonl"
    fake_claude(ui, tmp_path, 0.1)
    ui.ctx.settings.config_path.write_text(json.dumps({"dest_root": str(ui.ctx.settings.dest_root), "site_url": ui.ctx.settings.site_url, "llm": ui.ctx.settings.llm}))
    page = ui.page
    goto(ui, "settings")
    page.wait_for_selector(".mtable")
    assert page.locator(".mrow .mlabel").all_inner_texts() == ["Default", "Notes", "Study guide", "Audio script", "Chat", "Ask"]
    assert page.locator(".mrow[data-task=default] .mstatus").inner_text() == "haiku, sent to Anthropic"
    assert page.locator(".mrow[data-task=chat] .mstatus").count() == 0        # inherits: nothing more to say
    row = page.locator(".mrow[data-task=chat]")
    assert row.locator(".mextra").count() == 0
    row.locator("select").select_option("cli")
    row = page.locator(".mrow[data-task=chat]")
    assert page.locator("button", has_text="Save models").is_visible() and row.locator("button", has_text="Test").is_disabled()      # test what is saved, not a draft
    page.locator("button", has_text="Save models").click()                    # a command provider without a command is refused, with the reason
    page.wait_for_selector(".toast >> text=give the command to run")
    row.get_by_label("Chat: command").fill(shlex.join(FAKE_CLI + ["{model}"]))
    row.get_by_label("Chat: model", exact=True).fill("qwen3")
    row.get_by_label("Chat: runs on this computer").check()
    page.locator("button", has_text="Save models").click()
    page.wait_for_selector(".mrow[data-task=chat] .mstatus >> text=qwen3, stays on this computer")
    saved = json.loads(ui.ctx.settings.config_path.read_text())["llm"]["tasks"]
    assert saved == {"chat": {"provider": "cli", "command": FAKE_CLI + ["{model}"], "model": "qwen3", "local": True}}
    ui.ctx.settings.llm["tasks"]["chat"]["env"] = {"FAKE_CLI_LOG": str(log)}
    page.locator(".mrow[data-task=chat] button", has_text="Test").click()
    page.wait_for_selector(".toast >> text=Works: qwen3, stays on this computer")
    assert [r["args"] for r in runs(log)] == [["qwen3"]] and "Say: ready" in runs(log)[0]["stdin"]       # one tiny call, no course text
    page.locator(".mrow[data-task=chat] select").select_option("")           # back to the default: the row empties and the entry goes
    page.locator("button", has_text="Save models").click()
    page.wait_for_function("() => !document.querySelector('.mrow[data-task=chat] .mstatus')")
    assert "tasks" not in json.loads(ui.ctx.settings.config_path.read_text())["llm"]
    errors = [e for e in page.errors if e[0] in ("pageerror", "error")]
    assert len(errors) == 1 and "400" in errors[0][1], page.errors          # only the refused save above


# ---------------------------------------------------------------- the Create panel, whole-term notes, the Library
def lectures(ui, weeks=(1, 2)):
    for wk in weeks:
        d = ui.folder / "Lectures" / f"Week {wk:02d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{wk:02d} Topic.txt").write_text(f"Topic {wk}: " + "parsing " * 60)


def create(page):
    """Press Create, accept the confirm, and wait until the job is done: the page then drops the plan."""
    page.locator("#ai-create button.primary").click()
    page.locator("dialog[open] button.primary").click()
    page.wait_for_function("() => !document.querySelector('.ai-plan') && document.documentElement.dataset.busy === '0'", timeout=20000)


def test_create_panel_writes_one_note_per_category_for_the_whole_term(ui, tmp_path):
    log = tmp_path / "cli.jsonl"
    ui.ctx.settings.llm = local_cli(log, "# Term notes\n\nbody")
    lectures(ui)
    page = ui.page
    goto(ui, "ai")
    page.wait_for_selector("#ai-create")
    assert page.locator(".ai-card h2").all_inner_texts() == ["Create", "Chat with the course"]
    panel = page.locator("#ai-create")
    assert panel.get_by_role("group", name="Make").locator("button[aria-pressed=true]").inner_text() == "Notes"
    assert panel.get_by_role("group", name="Group").locator("button[aria-pressed=true]").inner_text() == "Per week"
    panel.get_by_role("button", name="Show plan").click()
    page.wait_for_selector(".ai-plan .ai-job")
    assert [t.split(" · ")[0] for t in page.locator(".ai-job .grow").all_inner_texts()] == ["Week 1 lectures", "Week 2 lectures"]
    panel.get_by_role("button", name="Per category (whole term)").click()
    assert page.locator(".ai-plan").count() == 0 and panel.get_by_label("Only this week").is_disabled()      # a new choice needs a new plan
    panel.get_by_role("button", name="Show plan").click()
    page.wait_for_selector(".ai-plan .ai-job")
    assert page.locator(".ai-job .grow").all_inner_texts() == ["Lectures, all weeks · from 2 files"]
    assert page.locator(".ai-job .badge").first.inner_text() == "Whole term"
    assert panel.locator("button.primary").inner_text() == "Create 1 note" and runs(log) == []
    create(page)
    out = ui.folder / "Notes" / "Lectures" / "Lectures - all weeks.md"
    assert out.exists() and "Topic 1" in runs(log)[0]["stdin"] and "Topic 2" in runs(log)[0]["stdin"] and len(runs(log)) == 1
    assert not (ui.folder / "Notes" / "Lectures" / "Week 01.md").exists()
    page.reload()                                                             # the choice is remembered
    page.wait_for_selector("#ai-create")
    assert page.locator("#ai-create").get_by_role("group", name="Group").locator("button[aria-pressed=true]").inner_text() == "Per category (whole term)"
    assert [e for e in page.errors if e[0] in ("pageerror", "error")] == [], page.errors


def test_study_guide_is_a_choice_under_make_and_the_library_lists_it_apart(ui, tmp_path):
    log = tmp_path / "cli.jsonl"
    ui.ctx.settings.llm = local_cli(log, "# Written\n\nbody")
    lectures(ui)
    page = ui.page
    goto(ui, "ai")
    panel = page.locator("#ai-create")
    panel.get_by_role("button", name="Show plan").click()
    page.wait_for_selector(".ai-plan .ai-job")
    create(page)                                                              # two weekly notes for the guide to read
    panel = page.locator("#ai-create")
    panel.get_by_role("button", name="Study guide").click()
    assert panel.get_by_role("group", name="Group").count() == 0 and panel.locator(".ai-kinds").count() == 0     # a guide has no kinds or weeks
    panel.get_by_role("button", name="Show plan").click()
    page.wait_for_selector(".ai-plan .ai-job")
    assert page.locator(".ai-job .badge").first.inner_text() == "Study guide" and panel.locator("button.primary").inner_text() == "Create 1 study guide"
    create(page)
    assert (ui.folder / "Notes" / "Study guide.md").exists()
    assert page.locator("#ai-lib").count() == 0
    page.locator(".phead button", has_text="Library").click()
    page.wait_for_selector("#ai-lib")
    text = page.locator("#ai-lib").inner_text()
    assert text.index("Notes") < text.index("Week 01.md") < text.index("Study guides") < text.index("Study guide.md")
    page.locator("#ai-lib .ai-gen", has_text="Study guide.md").get_by_role("button", name="Chat about it").click()
    assert "Study guide.md" in page.locator("#ai-chat .ai-chip").first.inner_text()
    page.locator(".phead button", has_text="Library").click()
    assert page.locator("#ai-lib").count() == 0
    assert [e for e in page.errors if e[0] in ("pageerror", "error")] == [], page.errors
