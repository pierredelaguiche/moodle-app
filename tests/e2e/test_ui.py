"""Browser tests (real Chromium via Playwright) for the pages and gestures. Skipped when Playwright/Chromium is not installed."""
import json
import re
import time

import pytest

pw = pytest.importorskip("playwright.sync_api")
from ui_seed import seed  # noqa: E402

MOD = "CSCI.999%20Test%20Module"


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                                   # no browser binary here
            pytest.skip(f"Chromium not available: {e}")
        yield b
        b.close()


@pytest.fixture
def ui(browser, tmp_path):
    env = seed(tmp_path)
    ctx = browser.new_context(viewport={"width": 1360, "height": 860})
    ctx.add_init_script("try { localStorage.setItem('guide.seen', '1') } catch (e) {}")      # the first-run redirect to Help is tested on its own
    page = ctx.new_page()
    page.errors = []
    page.on("console", lambda m: page.errors.append((m.type, m.text)) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: page.errors.append(("pageerror", str(e))))
    page.set_default_timeout(8000)
    page.goto(env.url)
    page.wait_for_selector("#nav .navitem")
    env.page = page
    yield env
    ctx.close()
    env.srv.shutdown()
    env.srv.server_close()
    env.conn.close()
    env.mock.stop()


def goto(ui, route):
    ui.page.evaluate("(r) => { location.hash = r; }", "#/" + route)
    ui.page.wait_for_timeout(250)


def expand_all(ui):
    """Smart allocation with every Moodle file listed (10 rows in two folders)."""
    goto(ui, "files/allocate")
    ui.page.get_by_role("button", name=re.compile("^Everything")).click()
    ui.page.wait_for_selector(".alloc .frow[data-sel]")


def sel_count(ui):
    return ui.page.locator(".frow.selected").count()


def wait_job(ui):
    ui.page.wait_for_function("() => document.querySelector('#joblog').hidden || document.querySelector('#joblog .jt').textContent !== 'Working…' && !document.querySelector('#joblog .spin').offsetParent")
    ui.page.wait_for_timeout(300)


# ---------------------------------------------------------------- pages
@pytest.mark.parametrize("route", ["", "files", "tracker", "calendar", "deadlines", "announcements", "history", "edit"])
def test_every_page_renders_without_console_or_csp_errors(ui, route):
    goto(ui, route)
    ui.page.wait_for_timeout(500)
    body = ui.page.inner_text("#main")
    assert "[object" not in body and "undefined" not in body and len(body) > 20
    assert ui.page.errors == []


def test_history_is_reachable_from_the_menu_and_from_announcements(ui):
    ui.page.click("#more")
    ui.page.get_by_role("menuitem", name="Activity log").click()
    ui.page.wait_for_selector("h1:has-text('Activity log')")
    assert ui.page.locator(".hist").count() > 0
    goto(ui, "announcements")
    ui.page.get_by_role("button", name="Activity log").click()
    ui.page.wait_for_selector("h1:has-text('Activity log')")
    assert not ui.page.locator("#nav >> text=History").count() and not ui.page.locator("#nav >> text=Activity").count()


def test_dashboard_digest_shows_focus_and_stats(ui):
    goto(ui, "")
    ui.page.wait_for_selector(".focus .f")
    txt = ui.page.inner_text("#main")
    assert "Do first" in txt and "Lab report" in txt and "Next 7 days" in txt and "Where you are in the term" in txt
    assert ui.page.locator(".stat").count() == 5


# ---------------------------------------------------------------- selection
def test_click_ctrl_click_shift_click_and_keyboard_selection(ui):
    expand_all(ui)
    rows = ui.page.locator(".frow[data-sel]")
    assert rows.count() == 10
    rows.nth(1).click()
    assert sel_count(ui) == 1
    rows.nth(4).click(modifiers=["Shift"])
    assert sel_count(ui) == 4                                   # rows 1..4
    rows.nth(7).click(modifiers=["Control"])
    assert sel_count(ui) == 5
    rows.nth(2).click(modifiers=["Control"])                    # toggle one off
    assert sel_count(ui) == 4
    assert ui.page.inner_text("#selbar .info") == "4 selected"
    rows.nth(0).click()
    assert sel_count(ui) == 1
    ui.page.keyboard.press("Control+a")
    assert sel_count(ui) == 10
    ui.page.keyboard.press("Escape")
    assert sel_count(ui) == 0 and "on" not in (ui.page.get_attribute("#selbar", "class") or "")
    rows.nth(0).click()
    ui.page.keyboard.press("Shift+ArrowDown")
    ui.page.keyboard.press("Shift+ArrowDown")
    assert sel_count(ui) == 3


def test_rubber_band_selects_rows_under_the_box_and_adds_with_ctrl(ui):
    expand_all(ui)
    rows = ui.page.locator(".frow[data-sel]")
    b0, b3 = rows.nth(0).bounding_box(), rows.nth(2).bounding_box()
    x = b0["x"] + b0["width"] - 20                                   # start in empty space at the right of a row
    ui.page.mouse.move(x, b0["y"] + 2)
    ui.page.mouse.down()
    ui.page.mouse.move(x - 60, b3["y"] + b3["height"] - 2, steps=8)
    ui.page.mouse.up()
    assert sel_count(ui) == 3
    assert ui.page.locator(".band").count() == 0
    b6 = rows.nth(6).bounding_box()
    ui.page.keyboard.down("Control")
    ui.page.mouse.move(x, b6["y"] + 2)
    ui.page.mouse.down()
    ui.page.mouse.move(x - 40, b6["y"] + b6["height"] - 2, steps=6)
    ui.page.mouse.up()
    ui.page.keyboard.up("Control")
    assert sel_count(ui) == 4


# ---------------------------------------------------------------- bulk actions
def test_download_selection_then_track_and_review(ui):
    expand_all(ui)
    ui.page.locator(".ghead", has_text="Workshops").click()
    assert sel_count(ui) == 4
    ui.page.locator("#selbar").get_by_role("button", name="Download 4").click()
    wait_job(ui)
    assert len(list((ui.folder / "Workshops").glob("*.pdf"))) == 4
    ui.page.wait_for_selector(".badge.b-downloaded")
    # mark two reviewed by keyboard in the Organise view
    goto(ui, "files/organise")
    ui.page.locator(".pane.right .frow.dir", has_text="Workshops").first.locator(".chev").click()
    ui.page.locator(".pane.right .frow.rf", has_text="Workshop").first.click()
    ui.page.keyboard.press("Shift+ArrowDown")
    ui.page.keyboard.press("r")
    ui.page.wait_for_selector(".rv")
    assert ui.page.locator(".rv").count() == 2
    goto(ui, "tracker")
    ui.page.wait_for_selector(".wk")
    assert ui.page.locator(".wk", has_text="W1").inner_text().split()[-1] == "1/3"                 # Workshop 1.1 is the only reviewed file of week 1
    totals = ui.page.inner_text(".course .ch")
    assert re.search(r"2/10", totals)


def test_ignore_has_undo_and_context_menu_works(ui):
    expand_all(ui)
    row = ui.page.locator(".frow[data-sel]", has_text="Lecture 1.0")
    row.click(button="right")
    ui.page.get_by_role("menuitem", name=re.compile("^Ignore")).click()
    ui.page.wait_for_selector(".toast >> text=Ignored 1")
    ui.page.wait_for_selector(".frow.st-ignored")
    assert ui.page.locator(".frow.st-ignored").count() == 1
    ui.page.locator(".toast button", has_text="Undo").click()
    wait_job(ui)
    ui.page.wait_for_function("() => !document.querySelector('.frow.st-ignored')")


def test_tracker_week_button_marks_whole_week_done(ui):
    ids = [k for k, p in ui.ctx.pending.items() if p.item.section_no == 3]
    ui.app.download(ids)
    for _ in range(50):
        if not (ui.app.job and ui.app.job.running):
            break
        time.sleep(0.1)
    goto(ui, "tracker")
    ui.page.locator(".wk", has_text="W3").click()
    ui.page.get_by_role("button", name="Mark whole week reviewed").click()
    ui.page.wait_for_selector(".wk.done")
    assert "W3" in ui.page.locator(".wk.done").inner_text()


# ---------------------------------------------------------------- deadlines
def test_deadline_done_button_and_touch_swipe(ui):
    goto(ui, "deadlines")
    ui.page.wait_for_selector(".item")
    item = ui.page.locator(".item", has_text="Coursework 1")
    assert item.locator("input[type=checkbox]").count() == 0 and item.locator(".selbox").count() == 1   # one box per row: the select box
    item.get_by_role("button", name="Mark done").click()
    ui.page.wait_for_function("() => ![...document.querySelectorAll('.item')].some(e => e.textContent.includes('Coursework 1'))")
    assert ui.ctx.deadlines.get("7:301:assign")["done"] == 1                      # done items leave the open list
    ui.page.locator("label:has-text('Show done') input").check()
    ui.page.wait_for_selector(".item.done")
    ui.page.locator(".item", has_text="Project plan").evaluate("""(el) => {
      const r = el.getBoundingClientRect(), y = r.top + r.height / 2;
      const ev = (t, x) => el.dispatchEvent(new PointerEvent(t, { pointerType: 'touch', pointerId: 7, clientX: x, clientY: y, bubbles: true }));
      ev('pointerdown', r.left + 40); ev('pointermove', r.left + 120); ev('pointermove', r.left + 220); ev('pointerup', r.left + 220); }""")
    ui.page.wait_for_function("() => [...document.querySelectorAll('.item')].some(e => e.textContent.includes('Project plan') && e.classList.contains('done'))")
    assert ui.ctx.deadlines.get("7:303:assign")["done"] == 1
    ui.page.locator(".toast button", has_text="Undo").last.click()
    ui.page.wait_for_function("() => ![...document.querySelectorAll('.item')].some(e => e.textContent.includes('Project plan') && e.classList.contains('done'))")


def test_deadlines_shift_range_and_bulk_hide(ui):
    goto(ui, "deadlines")
    ui.page.wait_for_selector(".item")
    items = ui.page.locator(".list .item")
    items.nth(0).click()
    items.nth(2).click(modifiers=["Shift"])
    assert ui.page.locator(".item.selected").count() == 3
    ui.page.get_by_role("button", name="Hide").click()
    ui.page.wait_for_function("() => document.querySelectorAll('.item.selected').length === 0")
    assert len(ui.ctx.deadlines.all_for_ics()) == 7                          # 10 deadlines, 3 hidden


# ---------------------------------------------------------------- announcements
def test_announcement_expands_and_marks_read_and_html_is_plain_text(ui):
    goto(ui, "announcements")
    ui.page.get_by_role("button", name="Notice 0").click()
    ui.page.wait_for_selector(".msg")
    assert "Hello" in ui.page.inner_text(".msg") and "<" not in ui.page.inner_text(".msg") and "alert" not in ui.page.inner_text(".msg")
    assert ui.ctx.announcements.recent(5, unread_only=True).__len__() == 2
    assert ui.page.errors == []


# ---------------------------------------------------------------- calendar
def test_calendar_add_edit_drag_and_delete_own_event(ui):
    goto(ui, "calendar")
    ui.page.get_by_role("button", name="Agenda").click()
    ui.page.get_by_role("button", name="Week").click()
    ui.page.get_by_role("button", name="Month").click()
    ui.page.get_by_role("button", name="Add event").click()
    dlg = ui.page.locator("dialog")
    dlg.get_by_label("Title").fill("Study block")
    dlg.get_by_role("button", name="Add", exact=True).click()
    ui.page.wait_for_selector(".ev.own")
    assert ui.page.locator(".ev.own").inner_text().endswith("Study block")
    assert len(ui.ctx.events.all()) == 1
    # drag onto a different day cell
    chip = ui.page.locator(".ev.own")
    today_cell = ui.page.locator(".day.today")
    target = ui.page.locator(".day:not(.today):not(.out)").first
    before = ui.ctx.events.all()[0]["start_ts"]
    chip.drag_to(target)
    ui.page.wait_for_function("() => document.querySelector('.toast')")
    after = ui.ctx.events.all()[0]["start_ts"]
    assert after != before and (after - before) % 86400 == 0
    # delete from the side panel
    ui.page.locator(".ev.own").click()
    ui.page.locator(".side").get_by_role("button", name="Delete").click()
    ui.page.locator("dialog").get_by_role("button", name="Delete").click()
    ui.page.wait_for_function("() => !document.querySelector('.ev.own')")
    assert ui.ctx.events.all() == []


def test_calendar_keyboard_navigation_and_week_view(ui):
    goto(ui, "calendar")
    title0 = ui.page.inner_text(".phead .sub")
    ui.page.keyboard.press("ArrowRight")
    ui.page.wait_for_function("(t) => document.querySelector('.phead .sub').textContent !== t", arg=title0)
    ui.page.keyboard.press("t")
    ui.page.wait_for_function("(t) => document.querySelector('.phead .sub').textContent === t", arg=title0)
    ui.page.keyboard.press("w")
    ui.page.wait_for_selector(".tgrid")
    assert ui.page.locator(".tcol").count() == 7


# ---------------------------------------------------------------- editor
def test_editor_saves_with_ctrl_s_and_keeps_indentation_and_lists(ui):
    p = ui.folder / "Notes" / "todo.txt"
    goto(ui, "edit/" + __import__("urllib.parse").parse.quote(f"CSCI.999 Test Module/Notes/todo.txt", safe=""))
    ta = ui.page.locator(".edbody textarea")
    ta.wait_for()
    ta.click()
    ui.page.keyboard.press("Control+End")
    ui.page.keyboard.type("three")
    assert "Unsaved" in ui.page.inner_text(".edbar")
    ui.page.keyboard.press("Control+s")
    ui.page.wait_for_function("() => document.querySelector('.edbar').textContent.includes('Saved')")
    assert p.read_text() == "one\ntwo\nthree"
    assert list((ui.home / "backups").rglob("*todo.txt"))                 # previous version kept outside the study folder


def test_editor_markdown_list_continuation_and_tab(ui):
    goto(ui, "edit/" + __import__("urllib.parse").parse.quote("CSCI.999 Test Module/Notes/week1.md", safe=""))
    ta = ui.page.locator(".edbody textarea")
    ta.wait_for()
    ta.click()
    ui.page.keyboard.press("Control+End")
    ui.page.keyboard.type("\n- first")
    ui.page.keyboard.press("Enter")
    ui.page.keyboard.type("second")
    assert ta.input_value().endswith("- first\n- second")
    ui.page.keyboard.press("Tab")
    assert ta.input_value().endswith("- second    ")
    ui.page.wait_for_selector(".md li:has-text('second')")


def test_editor_conflict_dialog_offers_overwrite(ui):
    p = ui.folder / "Notes" / "todo.txt"
    goto(ui, "edit/" + __import__("urllib.parse").parse.quote("CSCI.999 Test Module/Notes/todo.txt", safe=""))
    ta = ui.page.locator(".edbody textarea")
    ta.wait_for()
    ta.click()
    ui.page.keyboard.type("mine ")
    p.write_text("changed elsewhere\n")
    ui.page.keyboard.press("Control+s")
    ui.page.wait_for_selector("dialog >> text=changed on disk")
    ui.page.locator("dialog").get_by_role("button", name="Keep editing").click()
    assert p.read_text() == "changed elsewhere\n"
    ui.page.keyboard.press("Control+s")
    ui.page.locator("dialog").get_by_role("button", name="Overwrite").click()
    ui.page.wait_for_function("() => document.querySelector('.edbar').textContent.includes('Saved')")
    assert p.read_text() == "one\ntwo\nmine "


def test_editor_new_file_and_pdf_preview_and_tabs(ui):
    goto(ui, "edit")
    ui.page.get_by_role("button", name="create a new file").click()
    dlg = ui.page.locator("dialog")
    dlg.get_by_label("Path").fill("CSCI.999 Test Module/Notes/fresh.md")
    dlg.get_by_role("button", name="Create").click()
    ui.page.wait_for_selector(".edtab >> text=fresh.md")
    assert (ui.folder / "Notes" / "fresh.md").exists()
    goto(ui, "edit/" + __import__("urllib.parse").parse.quote("CSCI.999 Test Module/Notes/sheet.pdf", safe=""))
    ui.page.wait_for_selector(".edbody iframe")
    assert ui.page.locator(".edtab").count() == 2
    ui.page.locator(".edtab", has_text="sheet.pdf").locator(".x").click()
    ui.page.wait_for_function("() => document.querySelectorAll('.edtab').length === 1")


def test_unsaved_tab_asks_before_closing(ui):
    goto(ui, "edit/" + __import__("urllib.parse").parse.quote("CSCI.999 Test Module/Notes/todo.txt", safe=""))
    ta = ui.page.locator(".edbody textarea")
    ta.wait_for()
    ta.click()
    ui.page.keyboard.type("x")
    ui.page.locator(".edtab .x").click()
    ui.page.locator("dialog").get_by_role("button", name="Cancel").click()
    assert ui.page.locator(".edtab").count() == 1
    assert (ui.folder / "Notes" / "todo.txt").read_text() == "one\ntwo\n"


# ---------------------------------------------------------------- global
def test_command_palette_jumps_to_a_file_and_a_page(ui):
    goto(ui, "files/organise")                               # loads the tree
    ui.page.keyboard.press("Escape")
    ui.page.keyboard.press("Control+k")
    ui.page.locator("#palette input").fill("week1")
    ui.page.keyboard.press("Enter")
    ui.page.wait_for_selector(".edtab >> text=week1.md")
    ui.page.keyboard.press("Control+k")
    ui.page.locator("#palette input").fill("track")
    ui.page.keyboard.press("Enter")
    ui.page.wait_for_selector("h1:has-text('Tracker')")


def test_go_shortcuts_and_help_dialog(ui):
    goto(ui, "")
    ui.page.keyboard.press("g")
    ui.page.keyboard.press("c")
    ui.page.wait_for_selector("h1:has-text('Calendar')")
    ui.page.keyboard.press("?")
    ui.page.wait_for_selector("h2:has-text('Keyboard and mouse')")
    assert "help/keys" in ui.page.url


def test_sidebar_collapses_and_theme_toggles(ui):
    ui.page.keyboard.press("[")
    assert "collapsed" in ui.page.get_attribute("#app", "class")
    ui.page.keyboard.press("[")
    assert "collapsed" not in ui.page.get_attribute("#app", "class")
    goto(ui, "settings")
    ui.page.get_by_label("Theme").select_option("dark")
    assert ui.page.evaluate("document.documentElement.dataset.theme") == "dark"
    ui.page.get_by_label("Theme").select_option("system")
    assert ui.page.evaluate("document.documentElement.dataset.theme") is None


def test_check_moodle_button_runs_a_job_and_updates_badges(ui):
    ui.mock.discussions[61].append({"discussion": 6000, "subject": "Late news", "message": "<p>x</p>", "created": int(time.time()), "modified": int(time.time())})
    ui.page.get_by_role("button", name="Check Moodle").click()
    wait_job(ui)
    ui.page.wait_for_function("() => document.querySelector('#pill-announcements').textContent === '4'")


def test_phone_width_menu_drawer(ui):
    ui.page.set_viewport_size({"width": 390, "height": 800})
    ui.page.wait_for_timeout(200)
    assert ui.page.locator("#menubtn").is_visible()
    ui.page.click("#menubtn")
    assert "menuopen" in ui.page.get_attribute("#app", "class")
    ui.page.locator("#nav a", has_text="Calendar").click()
    ui.page.wait_for_selector("h1:has-text('Calendar')")
    assert "menuopen" not in ui.page.get_attribute("#app", "class")
    assert ui.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def test_markdown_preview_typesets_maths(ui):
    md = ui.folder / "Notes" / "maths.md"
    md.write_text("Derivation of `$abbbbc$`: $`$S \\Rightarrow aB \\Rightarrow abbbbc$`$\n\nPlain $x^2$ and a price of $5 or $6 stay.\n\n$$\n\\sum_{i=1}^n i = \\frac{n(n+1)}{2}\n$$\n\n```math\nE = mc^2\n```\n\n`$notmath` stays code and `a $ b` too.\n")
    goto(ui, "edit/" + __import__("urllib.parse").parse.quote("CSCI.999 Test Module/Notes/maths.md", safe=""))
    ui.page.wait_for_selector(".md .katex")
    prev = ui.page.locator(".md")
    assert prev.locator(".katex").count() == 5 and prev.locator(".math-block").count() == 2
    text = prev.inner_text()
    assert "$5 or $6" in text and "$notmath" in text and "$`" not in text
    assert prev.locator(".katex-error, .math-raw").count() == 0
    ui.page.wait_for_function("[...document.fonts].some(f => f.family.includes('KaTeX') && f.status === 'loaded')", timeout=5000)   # fonts pass the CSP


def test_help_page_quick_start_and_sidebar_button(ui):
    ui.page.click("#helpbtn")
    ui.page.wait_for_selector("h2:has-text('Quick start')")
    assert ui.page.locator(".gd-step").count() == 4
    assert ui.page.locator(".gd-step.done").count() >= 2          # the test setup has a folder, an address and a token
    for title in ("Files: downloading from Moodle", "AI notes", "Keyboard and mouse"):
        assert ui.page.get_by_role("heading", name=title, exact=True).count() == 1
    ui.page.click("#more")
    names = ui.page.get_by_role("menuitem").all_inner_texts()
    assert not any("Dark" in n for n in names)


def test_first_visit_opens_the_quick_start_when_setup_is_incomplete(ui):
    ui.app.x.settings.dest_root = None                       # as on a brand-new install
    fresh = ui.page.context.browser.new_context(viewport={"width": 1200, "height": 800})
    page = fresh.new_page()
    page.goto(ui.url)
    page.wait_for_selector("h2:has-text('Quick start')")
    assert "#/help" in page.url
    page.goto(ui.url + "#/")                                  # remembered: not forced again
    page.wait_for_selector("#nav .navitem")
    assert "#/help" not in page.url
    fresh.close()
