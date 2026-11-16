"""Browser tests for the Chat page (a fake `claude` command streams the answer), the resizable Study panels and the menu
whose icons stay put when it collapses."""
import os
import stat
import sys
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
from test_ui import browser, goto, ui  # noqa: E402,F401  (fixtures/helpers)
from moodle_app.services.chat import Chat  # noqa: E402

FAKE = Path(__file__).resolve().parent.parent / "fake_claude.py"
pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake command is a shell script")


@pytest.fixture
def chat(ui, tmp_path):
    exe = tmp_path / "bin" / "claude"
    exe.parent.mkdir()
    exe.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}' \"$@\"\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    ui.app._chat = Chat(ui.ctx, ui.app.lock, exe=str(exe))
    return ui


def test_chat_streams_an_answer_in_the_module_folder_and_remembers_it(chat, tmp_path):
    page = chat.page
    goto(chat, "chat")
    page.wait_for_selector(".ch-side select")
    assert "CSCI.999 Test Module" in page.locator(".ch-side select").inner_text()
    page.locator(".ch-ask textarea").fill("What is in this folder?")
    page.keyboard.press("Enter")                                             # Enter sends, like an editor's chat
    page.wait_for_selector(".msg.me >> text=What is in this folder?")
    page.wait_for_selector(".ch-ai .md >> text=The folder has")
    assert page.locator(".ch-tool .tn").first.inner_text() == "Glob"
    page.locator(".ch-tool summary").first.click()
    assert "Notes" in page.locator(".ch-tool pre").inner_text()               # the tool's output, on demand
    assert page.locator(".ch-ai .katex").count() == 1                        # maths typeset
    page.wait_for_selector(".ch-ai .minfo >> text=$0.0123")
    assert page.locator(".ch-bar h1").inner_text() == "What is in this folder?"
    assert page.locator(".ch-item").count() == 1
    # what Claude may do: picked per conversation; the dangerous one says so
    page.locator(".ch-opts select").first.select_option("run")
    page.wait_for_selector(".ch-warn")
    # another project: a folder from anywhere on the computer
    proj = tmp_path / "my-app"
    proj.mkdir()
    (proj / "main.py").write_text("print('hi')\n")
    page.get_by_role("button", name="+ Add folder").click()
    page.locator("dialog input").fill(str(proj))
    page.get_by_role("button", name="Add", exact=True).click()
    page.wait_for_selector(".ch-path >> text=my-app")
    assert page.locator(".ch-item").count() == 0                             # conversations belong to their project
    page.locator(".ch-ask textarea").fill("and here?")
    page.get_by_role("button", name="Send").click()
    page.wait_for_selector(".ch-ai .md >> text=main.py")
    # leave and come back: the project and its conversation are still there
    goto(chat, "")
    goto(chat, "chat")
    page.wait_for_selector(".ch-ai .md >> text=main.py")
    errs = [e for e in page.errors if e[0] == "pageerror"]
    assert errs == []


def test_study_side_panels_resize_and_keep_their_width(ui):
    page = ui.page
    goto(ui, "edit")
    page.wait_for_selector(".st-grip.l")
    w0 = page.locator(".st-side").bounding_box()["width"]
    g = page.locator(".st-grip.l").bounding_box()
    page.mouse.move(g["x"] + 4, 300)
    page.mouse.down()
    page.mouse.move(g["x"] + 104, 300, steps=4)
    page.mouse.up()
    assert abs(page.locator(".st-side").bounding_box()["width"] - (w0 + 100)) <= 2
    a0 = page.locator(".st-ai").bounding_box()["width"]
    g = page.locator(".st-grip.r").bounding_box()
    page.mouse.move(g["x"] + 4, 300)
    page.mouse.down()
    page.mouse.move(g["x"] - 76, 300, steps=4)
    page.mouse.up()
    assert abs(page.locator(".st-ai").bounding_box()["width"] - (a0 + 80)) <= 2
    page.reload()
    page.wait_for_selector(".st-grip.l")
    assert abs(page.locator(".st-side").bounding_box()["width"] - (w0 + 100)) <= 2      # kept
    page.locator(".st-grip.l").dblclick()
    assert abs(page.locator(".st-side").bounding_box()["width"] - 236) <= 2              # double-click: the default
    # the per-file "open beside" button stays in its row (it once took the look of the slide-over panel)
    row = page.locator(".st-file").first
    row.hover()
    rb, bb = row.bounding_box(), row.locator(".st-own").bounding_box()
    assert rb["x"] <= bb["x"] and bb["x"] + bb["width"] <= rb["x"] + rb["width"] + 1


def test_menu_icons_stay_put_when_it_collapses(ui):
    page = ui.page
    spots = "() => [...document.querySelectorAll('#nav .navitem svg, #palbtn svg, #helpbtn svg, #more svg, #side .logo')].map(e => { const r = e.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y)]; })"
    before = page.evaluate(spots)
    page.keyboard.press("[")
    page.wait_for_timeout(80)
    during = page.evaluate(spots)
    page.wait_for_timeout(300)
    after = page.evaluate(spots)
    assert before == during == after
    assert page.locator("#side").bounding_box()["width"] == 56
    page.keyboard.press("[")
    page.wait_for_timeout(300)
    assert page.evaluate(spots) == before
