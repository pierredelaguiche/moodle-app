"""Browser tests for the two Files views: smart allocation and organise (drag and drop, folders, preview, PDF text, selection)."""
import re
import time

import pytest

pw = pytest.importorskip("playwright.sync_api")
from test_ui import browser, goto, ui, wait_job as _wait_job  # noqa: E402,F401  (fixtures)

from mock_moodle import TOKEN as TOKEN_TEXT  # noqa: E402

MOD = "CSCI.999 Test Module"


def wait_job(ui):
    ui.page.wait_for_timeout(350)             # let the click reach the server and the log box open
    _wait_job(ui)


def rows(ui, scope=".pane.left"):
    """The file rows of a pane (group rows are not counted)."""
    return ui.page.locator(f"{scope} .frow[data-sel]:not([data-group])")


def row(ui, text, scope="body"):
    return ui.page.locator(f"{scope} .frow[data-sel]", has_text=text).first


def selected(ui, scope=""):
    return ui.page.locator(f"{scope} [data-sel].selected:not([data-group])").count()


def go_files(ui, show=None):
    """Open the Files page; `show` picks an entry of the status dropdown (todo, new, update, ignored, missing, downloaded, all)."""
    goto(ui, "files")
    ui.page.wait_for_selector(".org .pane.left .frow, .org .pane.left .empty")
    if show in ("todo", "new", "update", "ignored", "missing", "downloaded", "all"):
        ui.page.locator("select.show").select_option(show)
    ui.page.wait_for_timeout(250)


def group(ui, title):
    """A week (or course) heading row of the Moodle tree and the <li> that holds its files."""
    head = ui.page.locator(".pane.left .frow.lg", has_text=title).last
    return head, head.locator("xpath=..")


def on_disk(ui, rel):
    return (ui.root / rel).exists()


@pytest.fixture
def fui(ui):
    ui.root = ui.folder.parent
    return ui


def finish(ui):
    ui.page.wait_for_timeout(400)
    assert [e for e in ui.page.errors if e[0] in ("pageerror", "error")] == [], ui.page.errors


# ---------------------------------------------------------------- toggle and plain selection
def test_files_is_one_view_with_two_trees_and_no_native_checkboxes(fui):
    for route in ("files", "files/allocate", "files/organise"):       # old links still open the one view
        goto(fui, route)
        fui.page.wait_for_selector(".org .pane.left .frow")
        assert fui.page.locator(".org .pane").count() == 2 and fui.page.locator(".alloc, .fhead .seg").count() == 0
    assert fui.page.get_by_role("button", name="Smart allocation").count() == 0
    assert fui.page.locator(".org input[type=checkbox]").count() == 0     # the round box is drawn by the selection component
    assert fui.page.locator(".org .badge, .org .tag, .org .counts").count() == 0      # no flags on the rows
    assert fui.page.locator("select.show option").count() == 7
    finish(fui)


def test_status_dropdown_filters_and_bulk_download_and_ignore(fui):
    go_files(fui, "all")
    show = fui.page.locator("select.show")
    assert show.locator("option:checked").inner_text() == "Everything (10)"
    r = rows(fui)
    assert r.count() == 10
    r.nth(0).click()
    r.nth(2).click(modifiers=["Shift"])
    assert selected(fui) == 3
    bar = fui.page.locator("#selbar")
    assert "3 Moodle files selected" in bar.inner_text() and bar.get_by_role("button", name="Download 3").is_visible()
    bar.get_by_role("button", name="Ignore 3").click()
    fui.page.wait_for_timeout(500)
    assert selected(fui) == 3 and fui.page.locator(".frow.selected.st-ignored").count() == 3       # highlight follows the update
    show.select_option("ignored")
    assert rows(fui).count() == 3 and "Ignored (3)" in show.locator("option:checked").inner_text()
    show.select_option("new")
    assert rows(fui).count() == 7
    fui.page.locator(".toast button", has_text="Undo").click()
    wait_job(fui)
    show.select_option("all")
    assert fui.page.locator(".frow.st-ignored").count() == 0
    rows(fui).nth(0).click()
    fui.page.locator("#selbar").get_by_role("button", name="Download 1").click()
    wait_job(fui)
    assert fui.page.locator(".pane.left .frow.st-downloaded").count() == 1 and any(fui.folder.rglob("*.pdf"))
    show.select_option("downloaded")
    assert rows(fui).count() == 1
    goto(fui, "")                                                  # the choice is remembered while the tab is open
    go_files(fui)
    assert fui.page.locator("select.show").input_value() == "downloaded"
    finish(fui)


def test_download_all_new_follows_the_arrows(fui):
    go_files(fui)
    btn = fui.page.get_by_role("button", name="Download all new (10)")
    btn.click()
    wait_job(fui)
    assert len(list((fui.folder / "Lectures").glob("*.pdf"))) == 6 and len(list((fui.folder / "Workshops").glob("*.pdf"))) == 4
    fui.page.wait_for_function("() => document.querySelector('#dlall') && document.querySelector('#dlall').disabled")
    assert fui.page.locator(".arrows .ar").count() == 0             # nothing left to point at
    finish(fui)


def test_group_header_click_selects_every_file_in_the_week(fui):
    go_files(fui, "all")
    head, grp = group(fui, "Week 2")
    n = grp.locator(".frow.lf").count()
    head.click()
    assert n == 4 and selected(fui) == n and "selected" in head.get_attribute("class").split()
    group(fui, "Week 1")[0].click(modifiers=["Control"])
    assert selected(fui) == n + 3
    finish(fui)


def test_second_shift_click_only_moves_the_end_of_the_range(fui):
    """Regression: a second Shift+click used to drop a section selected earlier."""
    go_files(fui, "all")
    r = rows(fui)
    r.nth(0).click()
    r.nth(3).click(modifiers=["Control"])                          # a second section, anchored at row 3
    r.nth(6).click(modifiers=["Shift"])
    assert [r.nth(i).get_attribute("class").count("selected") > 0 for i in range(8)] == [True, False, False, True, True, True, True, False]
    r.nth(5).click(modifiers=["Shift"])                            # shrink the range: rows 0 stays, 6 leaves
    flags = [("selected" in r.nth(i).get_attribute("class")) for i in range(8)]
    assert flags == [True, False, False, True, True, True, False, False]
    r.nth(1).click(modifiers=["Control", "Shift"])
    assert "selected" in r.nth(0).get_attribute("class")
    finish(fui)


# ---------------------------------------------------------------- organise: drag and drop
def drag(ui, src, dst):
    src.drag_to(dst)
    ui.page.wait_for_timeout(500)


def test_drag_a_moodle_file_onto_a_folder_downloads_it_there(fui):
    go_files(fui)
    target = fui.page.locator(".pane.right .frow.dir", has_text="Notes")
    f = fui.page.locator(".pane.left .frow.lf", has_text="Workshop 1.1.pdf")
    drag(fui, f, target)
    wait_job(fui)
    assert on_disk(fui, f"{MOD}/Notes/Workshop 1.1.pdf")
    fui.page.locator("select.show").select_option("all")
    assert fui.page.locator(".pane.left .frow.lf.st-downloaded", has_text="Workshop 1.1.pdf").count() == 1
    fui.page.locator(".pane.right .frow.dir", has_text="Notes").locator(".chev").click()
    assert fui.page.locator(".pane.right .frow.rf", has_text="Workshop 1.1.pdf").count() == 1
    finish(fui)


def test_multi_select_in_moodle_pane_drops_all_at_once(fui):
    go_files(fui)
    left = fui.page.locator(".pane.left .frow.lf")
    left.nth(0).click()
    left.nth(2).click(modifiers=["Shift"])
    assert selected(fui, ".pane.left") >= 3
    n = selected(fui, ".pane.left")
    drag(fui, left.nth(1), fui.page.locator(".pane.right .frow.dir", has_text="Workshops"))
    wait_job(fui)
    got = [p for p in (fui.folder / "Workshops").glob("*.pdf")]
    assert len(got) >= n
    finish(fui)


def test_moving_inside_the_computer_tree_is_tracked_and_undoable(fui):
    go_files(fui)
    # a downloaded Moodle file, then moved by drag
    fui.page.locator(".pane.left .frow.lf", has_text="Workshop 1.1.pdf").click()
    fui.page.locator("#selbar").get_by_role("button", name="Download 1").click()
    wait_job(fui)
    fui.page.wait_for_selector(".pane.right")
    right = fui.page.locator(".pane.right")
    f = right.locator(".frow.rf", has_text="Workshop 1.1.pdf")
    if not f.is_visible():                                                  # the folder opens by itself when you pick a file that is going there
        right.locator(".frow.dir", has_text="Workshops").locator(".chev").click()
    assert fui.page.locator("#joblog, #actpanel").count() == 0              # nothing pops up over the folders when a job ends
    slow_drag(fui, f, right.locator(".frow.dir", has_text="Notes"))
    assert on_disk(fui, f"{MOD}/Notes/Workshop 1.1.pdf") and not on_disk(fui, f"{MOD}/Workshops/Workshop 1.1.pdf")
    rec = next(r for r in fui.ctx.files.all() if r.filename == "Workshop 1.1.pdf")
    assert rec.status == "downloaded" and rec.rel_path == f"{MOD}/Notes/Workshop 1.1.pdf"           # moved, not missing
    fui.page.locator(".toast button", has_text="Undo").click()
    fui.page.wait_for_timeout(500)
    assert on_disk(fui, f"{MOD}/Workshops/Workshop 1.1.pdf")
    assert next(r for r in fui.ctx.files.all() if r.filename == "Workshop 1.1.pdf").rel_path == f"{MOD}/Workshops/Workshop 1.1.pdf"
    finish(fui)


def test_a_folder_cannot_be_dropped_into_itself_and_module_folder_is_fixed(fui):
    go_files(fui)
    right = fui.page.locator(".pane.right")
    mod = right.locator(".frow.dir", has_text=MOD)
    assert mod.get_attribute("draggable") is None and mod.locator(".lockd").count() == 1      # a small lock, not a label
    notes = right.locator(".frow.dir", has_text="Notes").first
    notes.locator(".chev").click()
    drag(fui, notes, notes)
    assert on_disk(fui, f"{MOD}/Notes/week1.md")
    finish(fui)


def test_new_folder_move_to_dialog_and_rename(fui):
    go_files(fui)
    fui.page.locator(".pane.right .frow.dir.rootrow").click(button="right")     # extra actions live in the right-click menu
    fui.page.locator("#ctx").get_by_text("New folder").first.click()
    fui.page.locator("dialog input").fill(f"{MOD}/Research/Tools")
    fui.page.locator("dialog").get_by_role("button", name="Create").click()
    fui.page.wait_for_timeout(500)
    assert on_disk(fui, f"{MOD}/Research/Tools")
    fui.page.locator(".pane.right .frow.dir", has_text="Research").first.locator(".chev").click()
    # move a file with the picker
    notes = fui.page.locator(".pane.right .frow.dir", has_text="Notes").first
    notes.locator(".chev").click()
    fui.page.locator(".pane.right .frow.rf", has_text="todo.txt").click()
    fui.page.locator(".pane.right .frow.rf", has_text="todo.txt").click(button="right")
    fui.page.get_by_role("menuitem", name=re.compile("^Move 1 to")).click()
    fui.page.locator("dialog.picker li", has_text="Tools").click()
    fui.page.locator("dialog.picker").get_by_role("button", name="Move here").click()
    fui.page.wait_for_timeout(500)
    assert on_disk(fui, f"{MOD}/Research/Tools/todo.txt") and not on_disk(fui, f"{MOD}/Notes/todo.txt")
    # rename with F2
    fui.page.locator(".pane.right .frow.rf", has_text="todo.txt").click()
    fui.page.keyboard.press("F2")
    fui.page.locator("dialog input").fill("tasks.txt")
    fui.page.locator("dialog").get_by_role("button", name="Rename").click()
    fui.page.wait_for_timeout(500)
    assert on_disk(fui, f"{MOD}/Research/Tools/tasks.txt")
    finish(fui)


def test_subdivide_workshops_into_week_folders_with_undo(fui):
    go_files(fui)
    left = fui.page.locator(".pane.left .frow.lf")
    left.first.click()
    left.nth(left.count() - 1).click(modifiers=["Shift"])
    fui.page.locator("#selbar").get_by_role("button", name=re.compile("^Download \\d+$")).click()
    wait_job(fui)
    right = fui.page.locator(".pane.right")
    ws = right.locator(".frow.dir", has_text="Workshops").first
    assert fui.page.locator(".pane.right .acts:not(.focusbtn)").count() == 0      # no clutter on the rows (Focus shows only on hover)
    ws.click(button="right")
    fui.page.locator("#ctx").get_by_text("Split by week").click()
    dlg = fui.page.locator("dialog")
    assert "Week 01" in dlg.inner_text() and "new folder" in dlg.inner_text()
    dlg.get_by_role("button", name="Split by week").click()
    fui.page.wait_for_timeout(600)
    assert any(p.is_dir() and p.name.startswith("Week") for p in (fui.folder / "Workshops").iterdir())
    assert not list((fui.folder / "Workshops").glob("*.pdf"))
    fui.page.locator(".toast button", has_text="Undo").click()
    fui.page.wait_for_timeout(600)
    assert list((fui.folder / "Workshops").glob("*.pdf")) and not list((fui.folder / "Workshops").glob("Week*"))
    finish(fui)


# ---------------------------------------------------------------- preview and PDF text
def test_preview_of_a_moodle_file_saves_nothing_and_goes_away_with_the_page(fui):
    go_files(fui)
    before = sorted(p.relative_to(fui.root).as_posix() for p in fui.root.rglob("*"))
    fui.page.locator(".pane.left .frow.lf", has_text="Lecture 3.0.pdf").click()
    fui.page.keyboard.press("p")
    panel = fui.page.locator(".qlook")
    panel.wait_for(state="visible")
    assert "Preview only" in panel.inner_text()
    fui.page.wait_for_selector(".qlook iframe")
    assert fui.page.locator(".qlook iframe").get_attribute("src").startswith("blob:")
    assert sorted(p.relative_to(fui.root).as_posix() for p in fui.root.rglob("*")) == before
    assert not list((fui.home / "tmp" / "preview").glob("*")) if (fui.home / "tmp" / "preview").exists() else True
    panel.get_by_role("button", name="Close").click()
    assert fui.page.locator(".qlook").count() == 0
    fui.page.locator(".pane.left .frow.lf", has_text="Lecture 3.0.pdf").click()
    fui.page.keyboard.press("p")
    fui.page.wait_for_selector(".qlook")
    goto(fui, "deadlines")                                           # changing tab drops it
    goto(fui, "files/organise")
    fui.page.wait_for_selector(".org")
    assert fui.page.locator(".qlook").count() == 0
    assert sorted(p.relative_to(fui.root).as_posix() for p in fui.root.rglob("*")) == before
    finish(fui)


def test_preview_follows_the_selection_and_shows_local_text(fui):
    go_files(fui)
    right = fui.page.locator(".pane.right")
    right.locator(".frow.dir", has_text="Notes").first.locator(".chev").click()
    right.locator(".frow.rf", has_text="todo.txt").click()
    fui.page.keyboard.press("p")
    fui.page.wait_for_selector(".qlook .pvtext")
    assert "one" in fui.page.locator(".pvtext").inner_text() and "On this computer" in fui.page.locator(".qlook").inner_text()
    fui.page.keyboard.press("ArrowDown")                                   # overlay is open: arrows move to the next file
    fui.page.wait_for_function("() => document.querySelector('.pvtext') && document.querySelector('.pvtext').textContent.includes('Week 1')")
    finish(fui)


def test_preview_is_an_overlay_that_leaves_the_trees_alone(fui):
    go_files(fui)
    left = fui.page.locator(".pane.left")
    box = left.bounding_box()
    left.locator(".frow.lf", has_text="Lecture 3.0.pdf").click()
    fui.page.keyboard.press("p")
    ov = fui.page.locator(".qlook")
    ov.wait_for(state="visible")
    vp = fui.page.viewport_size
    ob = ov.bounding_box()
    assert ob["width"] >= vp["width"] - 2 and ob["height"] >= vp["height"] - 2          # covers the whole window
    assert left.bounding_box() == box                                                   # nothing was reorganised underneath
    fui.page.keyboard.press("Escape")
    assert fui.page.locator(".qlook").count() == 0
    assert left.locator(".frow.selected").count() == 1                                  # the selection survived
    fui.page.keyboard.press("p")
    fui.page.wait_for_selector(".qlook")
    fui.page.keyboard.press("Space")
    assert fui.page.locator(".qlook").count() == 0
    fui.page.keyboard.press("p")
    fui.page.wait_for_selector(".qlook")
    first = fui.page.locator(".qlook h2").inner_text()
    fui.page.keyboard.press("ArrowDown")
    fui.page.wait_for_function("t => document.querySelector('.qlook h2') && document.querySelector('.qlook h2').textContent !== t", arg=first)
    fui.page.mouse.click(5, 5)                                                          # click outside the panel
    assert fui.page.locator(".qlook").count() == 0
    finish(fui)


def test_pdf_text_is_not_offered_anywhere(fui):
    """Extraction is an internal step of notes: no menu entry, no key, no dialog."""
    go_files(fui)
    right = fui.page.locator(".pane.right")
    right.locator(".frow.dir", has_text="Notes").first.locator(".chev").click()
    right.locator(".frow.rf", has_text="sheet.pdf").click()
    fui.page.keyboard.press("t")
    fui.page.wait_for_timeout(300)
    assert fui.page.locator("dialog[open]").count() == 0
    right.locator(".frow.rf", has_text="sheet.pdf").click(button="right")
    names = fui.page.get_by_role("menuitem").all_inner_texts()
    assert names and not any(re.search(r"extract|read pdf|text", n, re.I) for n in names), names
    assert any(n.startswith("Create notes") for n in names)
    fui.page.keyboard.press("Escape")
    right.locator(".frow.dir", has_text="Notes").first.click(button="right")
    assert not any(re.search(r"read pdf", n, re.I) for n in fui.page.get_by_role("menuitem").all_inner_texts())
    fui.page.keyboard.press("Escape")
    finish(fui)


def test_double_click_a_tracked_file_in_moodle_pane_reveals_it_on_the_right(fui):
    go_files(fui)
    fui.page.locator(".pane.left .frow.lf", has_text="Workshop 1.1.pdf").click()
    fui.page.locator("#selbar").get_by_role("button", name="Download 1").click()
    wait_job(fui)
    fui.page.locator("select.show").select_option("all")
    fui.page.locator(".pane.left .frow.lf", has_text="Workshop 1.1.pdf").dblclick()
    assert fui.page.locator(".pane.right .frow.rf.selected", has_text="Workshop 1.1.pdf").count() == 1
    finish(fui)


def test_page_opens_before_the_check_finishes_and_shows_progress(ui, monkeypatch):
    from moodle_app.moodle.client import Moodle
    real = Moodle.call

    def slow(self, fn, **p):
        time.sleep(0.5)
        return real(self, fn, **p)
    monkeypatch.setattr(Moodle, "call", slow)
    ui.app.start("refresh", ui.app.refresh)          # what `serve` does right after it starts listening
    t0 = time.time()
    ui.page.goto(ui.url)
    ui.page.wait_for_selector("#nav .navitem")
    assert time.time() - t0 < 3, "the page must not wait for Moodle"
    ui.page.wait_for_selector(".sync.busy")
    assert ui.page.locator("#syncwhen").inner_text() == "Checking Moodle…"
    box = ui.page.locator("#activity")
    assert ui.page.locator("#actpanel").count() == 0 and "Checking Moodle" in box.inner_text() and re.search(r"Process #\d+", box.inner_text())   # a small box, no pop-up
    assert ui.page.locator("#procterm").is_hidden()
    ui.page.wait_for_selector(".sync:not(.busy)", timeout=60000)
    assert "Checked" in ui.page.locator("#syncwhen").inner_text()


# ---------------------------------------------------------------- the shared selection component
def box_opacity(ui, scope=".pane.left"):
    ui.page.wait_for_timeout(250)                                             # let the fade finish
    return ui.page.evaluate("s => [...document.querySelectorAll(s + ' [data-sel] > .selbox')].map(b => getComputedStyle(b).opacity)", scope)


def test_one_round_box_is_always_visible_on_every_row(fui):
    go_files(fui, "all")
    fui.page.mouse.move(5, 5)
    assert set(box_opacity(fui)) == {"1"}                                     # visible before anything is selected
    n = fui.page.evaluate("() => [...document.querySelectorAll('.org [data-sel]')].filter(r => r.querySelectorAll(':scope > .selbox').length !== 1).length")
    assert n == 0                                                             # exactly one per row
    rows(fui).nth(1).click()
    fui.page.keyboard.press("Escape")
    assert set(box_opacity(fui)) == {"1"}
    finish(fui)


def test_clicking_a_box_toggles_and_shift_on_a_box_extends(fui):
    go_files(fui, "all")
    r = rows(fui)
    r.nth(0).locator(".selbox").click()
    r.nth(2).locator(".selbox").click()
    assert selected(fui) == 2 and "selected" in r.nth(0).get_attribute("class") and "selected" in r.nth(2).get_attribute("class")
    r.nth(2).locator(".selbox").click()                                       # untick it again; the other stays
    assert selected(fui) == 1
    r.nth(4).locator(".selbox").click(modifiers=["Shift"])                    # range from the anchor
    assert selected(fui) == 5 - 0 and all("selected" in r.nth(i).get_attribute("class") for i in range(5)) or selected(fui) >= 4
    r.nth(6).click()                                                          # a plain click on a row replaces the selection
    assert selected(fui) == 1
    finish(fui)


def test_group_box_is_half_checked_and_selects_the_week(fui):
    go_files(fui, "all")
    head, grp = group(fui, "Week 2")
    grp.locator(".frow.lf").first.click()
    assert "partial" in head.get_attribute("class").split() and "selected" not in head.get_attribute("class").split()
    head.locator(".selbox").click()                                           # box on the heading: everything in the week
    assert "selected" in head.get_attribute("class").split() and selected(fui) == grp.locator(".frow.lf").count()
    head.locator(".selbox").click()                                           # and again: nothing left
    assert selected(fui) == 0
    finish(fui)


def test_collapsing_keeps_the_selection_and_changing_the_filter_drops_what_you_cannot_see(fui):
    go_files(fui, "all")
    head, grp = group(fui, "Week 2")
    head.locator(".selbox").click()
    n = selected(fui)
    assert n == 4
    head.locator(".chev").click()                                             # collapse: the files are hidden, still selected, and the bar says how many
    assert grp.locator(".frow.lf").first.is_hidden() and "selected" in head.get_attribute("class").split()
    assert f"{n} Moodle files selected" in fui.page.locator("#selbar").inner_text()
    fui.page.locator("select.show").select_option("ignored")                  # other filter: those files are gone from the page, so from the selection
    assert not fui.page.locator("#selbar.on").count() or "Download" not in fui.page.locator("#selbar").inner_text()
    assert selected(fui) <= 1
    finish(fui)


def test_select_all_in_the_bar_and_ctrl_a(fui):
    go_files(fui, "all")
    rows(fui).nth(0).click()
    fui.page.get_by_role("button", name=re.compile("^Select all")).click()
    assert selected(fui) == rows(fui).count()
    fui.page.keyboard.press("Escape")
    assert selected(fui) == 0
    rows(fui).nth(0).click()
    fui.page.keyboard.press("Control+a")
    assert selected(fui) == rows(fui).count()
    finish(fui)


def test_quick_look_steps_through_the_moodle_files(fui):
    go_files(fui, "all")
    rows(fui).nth(0).click()
    fui.page.keyboard.press("p")
    fui.page.wait_for_selector(".qlook")
    first = fui.page.locator(".qlook h2").inner_text()
    fui.page.keyboard.press("ArrowDown")
    fui.page.wait_for_function("t => document.querySelector('.qlook h2') && document.querySelector('.qlook h2').textContent !== t", arg=first)
    fui.page.keyboard.press("Escape")
    assert fui.page.locator(".qlook").count() == 0
    assert selected(fui) == 1
    finish(fui)


def test_group_row_in_the_moodle_tree_selects_its_files_and_drags_them(fui):
    go_files(fui)
    wk = fui.page.locator(".pane.left .frow.lg", has_text="Week 3")
    wk.locator(".selbox").click()
    assert selected(fui, ".pane.left") == 2 and "selected" in wk.get_attribute("class").split()
    assert "2 Moodle files selected" in fui.page.locator("#selbar").inner_text()
    finish(fui)


def slow_drag(ui, src, dst):
    """Mouse down, a few small moves, then a glide to the target: how a hand drags (Playwright's one-jump drag_to is unreliable for rows that sit next to their target)."""
    a, t = src.bounding_box(), dst.bounding_box()
    m = ui.page.mouse
    m.move(a["x"] + 40, a["y"] + a["height"] / 2); m.down()
    m.move(a["x"] + 50, a["y"] + a["height"] / 2 + 8, steps=3)
    m.move(t["x"] + 60, t["y"] + t["height"] / 2, steps=15)
    m.up()
    ui.page.wait_for_timeout(500)


# ---------------------------------------------------------------- the suggestion arrows: Moodle file -> the folder it will be saved in
def cls(el, prefix="c"):
    return next(c for c in el.get_attribute("class").split() if re.fullmatch(prefix + r"\d", c))


ARROW_ENDS = """() => {
  const host = document.querySelector('.org').getBoundingClientRect();
  return [...document.querySelectorAll('.arrows .ar.on .ln')].map((p) => {
    const n = p.getAttribute('d').match(/-?[\\d.]+/g).map(Number);
    return { x1: n[0] + host.left, y1: n[1] + host.top, x2: n[n.length - 2] + host.left, y2: n[n.length - 1] + host.top };
  });
}"""


def test_an_arrow_points_from_each_new_file_to_the_folder_it_will_be_saved_in(fui):
    go_files(fui)
    page = fui.page
    page.wait_for_function("() => document.querySelectorAll('.arrows .ar').length > 0")
    assert page.locator(".pane.right .frow.gh").count() == 0                        # no placeholder rows: the arrow says it
    left = page.locator(".pane.left .frow.lf", has_text="Lecture 3.0.pdf")
    other = page.locator(".pane.left .frow.lf", has_text="Workshop 3.1.pdf")
    lec, wsh = page.locator(".pane.right .frow.dir", has_text="Lectures"), page.locator(".pane.right .frow.dir", has_text="Workshops")
    assert left.locator(".to").bounding_box()["width"] <= 1 and "Lectures" in left.locator(".to").text_content()   # read out, not shown
    assert "Will be saved in Lectures (suggested)" in left.get_attribute("title")
    assert lec.locator(".inc.direct").inner_text() == "+6" and wsh.locator(".inc.direct").inner_text() == "+4"
    assert cls(left) == cls(lec.locator(".inc.direct")) != cls(other) == cls(wsh.locator(".inc.direct"))   # one colour per destination
    page.mouse.move(5, 5)
    assert page.locator(".arrows .ar").count() == 10 and page.locator(".arrows .ar.on").count() == 0
    left.click()                                                                    # picking a file makes its arrow strong and lights up the folder
    page.wait_for_selector(".arrows .ar.on")
    page.mouse.move(5, 5)
    page.wait_for_timeout(150)
    assert page.locator(".arrows .ar.on").count() == 1 and page.locator(".pane.right .frow.dir.hl").count() == 1
    assert "Lectures" in page.locator(".pane.right .frow.dir.hl").inner_text()
    end, a, b = page.evaluate(ARROW_ENDS)[0], left.bounding_box(), lec.bounding_box()
    assert a["y"] <= end["y1"] <= a["y"] + a["height"] and abs(end["x1"] - (a["x"] + a["width"])) < 12      # starts at the file...
    assert b["y"] <= end["y2"] <= b["y"] + b["height"] and b["x"] - 4 <= end["x2"] <= b["x"] + 60           # ...and ends on its folder
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    assert page.locator(".arrows .ar.on").count() == 0 and page.locator(".pane.right .frow.dir.hl").count() == 0
    wsh.hover()                                                                     # pointing at a folder shows everything that goes into it
    page.wait_for_function("() => document.querySelectorAll('.arrows .ar.on').length === 4")
    wsh.locator(".inc.direct").click()                                              # and its count selects those files
    assert selected(fui, ".pane.left") == 4 and "4 Moodle files selected" in page.locator("#selbar").inner_text()
    finish(fui)


def test_arrows_follow_scrolling_collapsing_and_a_folder_inside_a_closed_one(fui):
    go_files(fui)
    page = fui.page
    page.wait_for_function("() => document.querySelectorAll('.arrows .ar').length === 10")
    head, grp = group(fui, "Week 2")
    head.locator(".chev").click()                                                   # a closed week: its files start at the week row
    page.wait_for_function("() => document.querySelectorAll('.arrows .ar').length === 8")       # 4 files -> 2 arrows (one per folder)
    head.hover()
    page.wait_for_function("() => document.querySelectorAll('.arrows .ar.on').length === 2")
    h_box = head.bounding_box()
    assert all(h_box["y"] <= e["y1"] <= h_box["y"] + h_box["height"] for e in page.evaluate(ARROW_ENDS))
    mod = page.locator(".pane.right .frow.dir", has_text=MOD)
    mod.locator(".chev").click()                                                    # the module folder closed: arrows end on it
    page.mouse.move(5, 5)
    page.wait_for_function("() => document.querySelectorAll('.pane.right .frow.dir').length && !document.querySelector('.arrows .ar.on')")
    assert mod.locator(".inc.total").inner_text() == "+10" and mod.locator(".inc.direct").count() == 0
    page.locator(".pane.left .frow.lf", has_text="Lecture 1.0.pdf").hover()
    page.wait_for_selector(".arrows .ar.on")
    m_box = mod.bounding_box()
    assert all(m_box["y"] <= e["y2"] <= m_box["y"] + m_box["height"] for e in page.evaluate(ARROW_ENDS))
    page.set_viewport_size({"width": 900, "height": 860})                          # panes stacked: no arrows, the destination is written on the row
    page.wait_for_function("() => document.querySelector('.org').classList.contains('stacked') && !document.querySelector('.arrows .ar')")
    assert "Lectures" in page.locator(".pane.left .frow.lf", has_text="Lecture 1.0.pdf").locator(".to").inner_text()
    finish(fui)


def test_arrow_option_and_no_standing_arrow_for_downloaded_files(fui):
    go_files(fui, "all")
    page = fui.page
    page.wait_for_function("() => document.querySelectorAll('.arrows .ar').length === 10")
    first = page.locator(".pane.left .frow.lf", has_text="Lecture 1.0.pdf")
    first.click()
    page.locator("#selbar").get_by_role("button", name=re.compile("^Download 1")).click()
    wait_job(fui)
    page.keyboard.press("Escape")
    page.mouse.move(5, 5)
    page.wait_for_function("() => document.querySelectorAll('.arrows .ar').length === 9")      # downloaded: its arrow is gone
    assert page.locator(".arrows .ar.here").count() == 0
    first = page.locator(".pane.left .frow.lf.st-downloaded", has_text="Lecture 1.0.pdf")
    first.click()                                                                   # select it: a quiet arrow shows where it is
    page.wait_for_selector(".arrows .ar.here.on")
    assert page.locator(".arrows .ar.here").count() == 1
    page.keyboard.press("Escape")
    page.mouse.move(5, 5)
    page.wait_for_function("() => !document.querySelector('.arrows .ar.here')")
    page.locator("#arrowmode").select_option("picked")                              # only what is selected or pointed at
    page.wait_for_function("() => document.querySelectorAll('.arrows .ar').length === 0")
    page.locator(".pane.left .frow.lf", has_text="Workshop 1.1.pdf").click()
    page.wait_for_function("() => document.querySelectorAll('.arrows .ar').length === 1")
    goto(fui, "")
    go_files(fui)
    assert page.locator("#arrowmode").input_value() == "picked"                     # remembered
    finish(fui)


def test_expand_and_collapse_on_the_moodle_side(fui):
    go_files(fui)
    left = fui.page.locator(".pane.left")
    assert left.locator(".frow.lf:visible").count() == 10
    left.get_by_role("button", name="Collapse").click()
    assert left.locator(".frow.lf:visible").count() == 0 and left.locator(".frow.lg:visible").count() == 5     # the course stays open, its weeks close
    fui.page.wait_for_function("() => document.querySelectorAll('.arrows .ar').length === 7")    # arrows now start at the weeks, one per week and folder
    left.get_by_role("button", name="Expand").click()
    assert left.locator(".frow.lf:visible").count() == 10
    finish(fui)


def test_change_destination_repoints_the_arrow_without_downloading(fui):
    go_files(fui)
    page = fui.page
    before = sorted(p.relative_to(fui.root).as_posix() for p in fui.root.rglob("*"))
    row_ = page.locator(".pane.left .frow.lf", has_text="Lecture 1.0.pdf")
    row_.click(button="right")
    page.get_by_role("menuitem", name=re.compile("^Change destination")).click()
    dlg = page.locator("dialog.picker")
    dlg.locator("li", has_text="Notes").click()
    dlg.get_by_role("button", name="Use this folder").click()
    page.wait_for_function("() => [...document.querySelectorAll('.pane.left .frow.lf .to')].some(e => e.textContent.endsWith('Notes'))")
    assert sorted(p.relative_to(fui.root).as_posix() for p in fui.root.rglob("*")) == before          # nothing was downloaded
    assert "(your choice)" in row_.get_attribute("title")
    assert page.locator(".pane.right .frow.dir", has_text="Notes").locator(".inc.direct").inner_text() == "+1"
    row_.click()
    page.wait_for_selector(".arrows .ar.on.mine")                                   # your own choice is a solid line
    page.locator("#selbar").get_by_role("button", name=re.compile("^Download 1")).click()             # downloading follows the arrow
    wait_job(fui)
    assert (fui.folder / "Notes" / "Lecture 1.0.pdf").exists()
    page.wait_for_selector(".pane.right .frow.rf.arrive", state="attached")         # the row that has just landed glows once
    finish(fui)


def test_a_folder_divided_by_weeks_gets_the_arrow_to_the_week(fui):
    (fui.folder / "Lectures" / "Week 1").mkdir(parents=True)                        # Lectures is divided by weeks
    fui.app.refresh()
    go_files(fui)
    page = fui.page
    page.wait_for_function("() => document.querySelectorAll('.arrows .ar').length > 0")
    page.locator(".pane.left .frow.lf", has_text="Lecture 3.0.pdf").click()
    hl = page.locator(".pane.right .frow.dir.hl")
    assert hl.count() == 1 and "Week 3" in hl.inner_text() and "will be created" in hl.inner_text()
    page.locator("#selbar").get_by_role("button", name=re.compile("^Download 1")).click()
    wait_job(fui)
    assert (fui.folder / "Lectures" / "Week 3" / "Lecture 3.0.pdf").exists()
    assert not (fui.folder / "Workshops" / "Week 3").exists()                       # Workshops is not divided: untouched
    finish(fui)


def test_rows_keep_their_height_when_you_hover_them(fui):
    go_files(fui)
    fui.page.locator(".pane.right .frow.dir", has_text="Notes").first.locator(".chev").click()
    ys = lambda: fui.page.evaluate("() => [...document.querySelectorAll('.pane.right .frow')].filter(r => r.offsetParent).map(r => Math.round(r.getBoundingClientRect().y))")
    before = ys()
    assert len(before) > 5
    fui.page.locator(".pane.right .frow.dir", has_text="Lectures").first.hover()          # its buttons appear
    fui.page.wait_for_timeout(150)
    assert ys() == before
    fui.page.mouse.move(5, 5)
    fui.page.wait_for_timeout(150)
    assert ys() == before
    finish(fui)


def test_ignored_files_are_not_to_come(fui):
    go_files(fui)
    right = fui.page.locator(".pane.right")

    def to_come():
        return sum(int(t.lstrip("+")) for t in right.locator(".inc.direct").evaluate_all("els => els.map(e => e.textContent)"))
    before = to_come()
    assert before > 0
    row = fui.page.locator(".pane.left .frow.lf.st-new").first
    row.click(button="right")
    fui.page.get_by_role("menuitem", name=re.compile("^Ignore")).click()
    fui.page.wait_for_selector(".toast >> text=Ignored")
    fui.page.wait_for_timeout(600)
    assert to_come() < before
    finish(fui)


def test_ai_page_sits_between_files_and_tracker_and_files_hands_notes_over(fui):
    names = fui.page.locator("#nav .navitem[data-nav]").evaluate_all("els => els.map(e => e.dataset.nav)")
    assert names.index("files") + 1 == names.index("ai") and names.index("ai") + 1 == names.index("tracker")
    go_files(fui)
    right = fui.page.locator(".pane.right")
    right.locator(".frow.dir", has_text="Notes").first.click(button="right")
    fui.page.get_by_role("menuitem", name=re.compile("^Create notes")).click()
    fui.page.wait_for_selector(".ai-card")
    assert "#/ai" in fui.page.url and fui.page.locator(".ai-card h2", has_text="Note-taker").count() == 1
    assert "selected item" in fui.page.locator(".ai-card", has=fui.page.locator("h2", has_text="Note-taker")).inner_text()
    for h2 in ("Note-taker", "Prompts", "Ask"):
        assert fui.page.locator(".ai-card h2", has_text=h2).count() == 1
    assert fui.page.locator(".ai-card h2", has_text="Read").count() == 0          # extraction is not a step you run


def test_ai_page_plans_asks_and_edits_a_prompt(fui, tmp_path):
    import json as _json
    (tmp_path / "o.json").write_text(_json.dumps({"result": "The answer is 42.", "is_error": False, "stop_reason": "end_turn", "usage": {"input_tokens": 9, "output_tokens": 4}}))
    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\ncat >/dev/null\ncat {tmp_path}/o.json\n")
    fake.chmod(0o755)
    fui.ctx.settings.llm = {"provider": "claude_cli", "model": "haiku", "executable": str(fake)}
    src = fui.folder / "Lectures" / "L9.txt"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("Lecture nine says the answer is 42.")
    fui.page.evaluate("location.hash = '#/ai'")
    fui.page.wait_for_selector(".ai-card")
    fui.page.get_by_role("button", name="Show plan").click()
    fui.page.wait_for_selector(".ai-plan .ai-job")
    assert "L9" in fui.page.locator(".ai-plan").inner_text()
    fui.page.locator(".ai-files label", has_text="L9.txt").locator("input").check()
    fui.page.get_by_placeholder(re.compile("Ask about")).fill("What is the answer?")
    fui.page.get_by_role("button", name="Ask", exact=True).click()
    fui.page.wait_for_selector(".ai-answer")
    assert "42" in fui.page.locator(".ai-answer").inner_text()
    ta = fui.page.get_by_label("Prompt text")
    ta.fill(ta.input_value() + "\nBe brief.")
    fui.page.get_by_role("button", name="Save", exact=True).click()
    fui.page.wait_for_selector(".toast >> text=Saved")
    assert "Be brief." in (fui.ctx.settings.home / "prompts" / "lecture_notes.md").read_text()


def test_settings_page_changes_the_launch_option(fui):
    fui.page.evaluate("location.hash = '#/settings'")
    fui.page.wait_for_selector(".set-nav")
    fui.page.locator(".set-nav .navitem", has_text="Launch").click()
    fui.page.wait_for_selector(".set-card")
    fui.page.get_by_label("When the app starts").select_option("none")
    fui.page.get_by_role("button", name="Save changes").click()
    fui.page.wait_for_selector(".toast >> text=Saved")
    assert fui.ctx.settings.open_on_launch == "none"
    assert '"open_on_launch": "none"' in fui.ctx.settings.config_path.read_text()


def test_settings_open_on_connections_with_one_card_per_connector(fui):
    page = fui.page
    page.evaluate("location.hash = '#/settings'")
    page.wait_for_selector(".conn")
    assert page.locator(".set-nav .navitem[aria-current=page]").inner_text().strip() == "Connections"
    assert page.locator(".conn").evaluate_all("els => els.map(e => e.dataset.id)") == ["moodle", "folder", "model", "calendar"]
    moodle = page.locator(".conn[data-id=moodle]")
    assert "Signed in" in moodle.inner_text() and "1 course matched" in moodle.inner_text() and "s-ok" in moodle.get_attribute("class")
    assert str(fui.root) in page.locator(".conn[data-id=folder]").inner_text()
    assert page.locator(".conn[data-id=calendar] a[download]").get_attribute("href").startswith("/calendar.ics?k=")
    assert TOKEN_TEXT not in page.locator(".set-body").inner_text()                # the Moodle token is never shown
    page.locator(".conn[data-id=folder]").get_by_role("button", name="Change…").click()      # an action leads to the matching options
    page.wait_for_selector(".set-card")
    assert page.get_by_label("Study folder").input_value() == str(fui.root) and "#/settings/Folders" in page.url
    page.get_by_label("Time zone").fill("Europe/Zurich")
    page.locator(".set-nav .navitem", has_text="Sync").click()                     # the option groups are one scrolling page: no question,
    page.wait_for_function("() => { const r = document.getElementById('set-Sync').getBoundingClientRect(); return r.top >= 0 && r.top < innerHeight; }")
    assert page.locator(".set-card[data-group]").count() >= 5 and page.get_by_label("Time zone").input_value() == "Europe/Zurich"   # the edit is kept
    page.locator(".set-nav .navitem", has_text="Appearance").click()               # leaving the options with unsaved changes asks first
    page.locator("dialog").get_by_role("button", name="Cancel").click()
    assert page.get_by_label("Time zone").input_value() == "Europe/Zurich"
    page.keyboard.press("Escape")
    page.locator("#more").click()
    page.get_by_role("menuitem", name="Connections").click()
    page.wait_for_selector(".conn")
    finish(fui)


# ---------------------------------------------------------------- activity: quiet, concurrent, cancellable
def test_downloads_run_in_the_background_with_a_quiet_line_and_can_be_stacked_and_cancelled(fui, monkeypatch):
    from moodle_app.moodle.client import Moodle
    real = Moodle.download

    def slow(self, url, dest):
        time.sleep(0.6)
        real(self, url, dest)
    monkeypatch.setattr(Moodle, "download", slow)
    fui.ctx.settings.sync["parallel_downloads"] = 2
    go_files(fui)
    page = fui.page
    group(fui, "Week 2")[0].locator(".selbox").click()
    page.locator("#selbar").get_by_role("button", name="Download 4").click()
    act = page.locator("#activity")
    page.wait_for_selector("#activity.on")
    assert "Downloading 4 files" in act.inner_text() and page.locator("#actpanel, #joblog, dialog[open]").count() == 0   # no pop-up
    box = page.locator("#proc").bounding_box()
    assert re.match(r"Process #\d+", act.inner_text()) and box["y"] + box["height"] <= page.locator(".sync").bounding_box()["y"] + 1   # "Process #n", on top of Check Moodle
    assert page.locator("#procterm").is_hidden()
    act.click()                                                                  # open it: the terminal of that process, live
    page.wait_for_function("() => document.querySelector('#procterm pre').textContent.includes('Downloading 4')")
    assert act.get_attribute("aria-expanded") == "true"
    act.click()
    assert page.locator("#procterm").is_hidden()
    page.wait_for_selector(".arrows .ar.flow")                                   # the arrows of what is on its way are moving
    page.keyboard.press("Escape")                                                # the page is not blocked: select and start more
    page.locator(".pane.left .frow.lf", has_text="Lecture 1.0.pdf").click()
    page.locator("#selbar").get_by_role("button", name="Download 1").click()
    page.wait_for_function("() => /^Processes #\\d+, #\\d+/.test(document.querySelector('#activity .pn').textContent)")    # one more is waiting: both are named
    page.locator("#activity").click()
    page.locator("#procall").click()                                             # every process: waiting, running, finished
    panel = page.locator("#actpanel")
    panel.wait_for()
    rows_ = panel.locator(".actrow")
    assert rows_.count() >= 2 and "waiting" in rows_.first.inner_text() and "Downloading 1 file" in rows_.first.inner_text()
    rows_.first.get_by_role("button", name="Cancel").click()                     # drop the waiting one
    page.wait_for_function("() => document.querySelector('#actpanel .actrow').textContent.includes('cancelled')")
    running = panel.locator(".actrow.s-running")
    running.get_by_role("button", name="Log").click()
    page.wait_for_function("() => (document.querySelector('#actpanel .actlog') || {}).textContent && document.querySelector('#actpanel .actlog').textContent.includes('Downloading 4')")
    page.keyboard.press("Escape")
    assert page.locator("#actpanel").count() == 0
    wait_job(fui)
    page.wait_for_selector("#activity.ok")                                       # the box stays, so the terminal can still be read afterwards
    assert len(list(fui.folder.rglob("*.pdf"))) == 1 + 4                         # sheet.pdf + the four; the cancelled one never started
    assert not (fui.folder / "Lectures" / "Lecture 1.0.pdf").exists()
    idle = page.locator("#activity").inner_text()
    assert idle.startswith("Nothing running") and re.search(r"Last: Downloading 4 files · done", idle) and "#" not in idle and "undefined" not in idle
    assert "saved" in page.locator("#procterm pre").text_content()                # opened earlier: now the terminal of what finished last
    page.locator("#proclog").click()                                             # and a way to the activity log
    page.wait_for_function("() => location.hash === '#/history'")
    page.keyboard.press("[")                                                     # icons-only menu: the box shrinks to its number, nothing overflows
    page.wait_for_timeout(250)
    side = page.locator("#side").bounding_box()
    over = page.evaluate("w => [...document.querySelectorAll('#side *')].filter(e => e.offsetParent && e.getBoundingClientRect().right > w + 0.5).map(e => e.id || e.className)", side["x"] + side["width"])
    assert over == [] and page.locator("#activity .pm").inner_text() == "–"
    finish(fui)


def test_courses_section_lists_unfollowed_courses_and_follows_one(fui):
    fui.mock.courses.append({"id": 21, "fullname": "Intro to Programming: Year 1", "shortname": "Y1PROG"})
    fui.mock.sections[21] = []
    fui.mock.add_file(21, 1, 900, "Old lecture.pdf", b"%PDF-1.4 old")
    fui.app.refresh()
    page = fui.page
    page.evaluate("location.hash = '#/settings/courses'")
    page.wait_for_selector(".crs")
    row = page.locator(".crs[data-id='21']")
    assert "Not followed" in row.inner_text() and "matched by its module code" in page.locator(".crs[data-id='7']").inner_text()
    row.get_by_role("button", name="Follow…").click()
    dlg = page.locator("dialog")
    assert dlg.locator("input").input_value() == "Intro to Programming Year 1"
    dlg.get_by_role("button", name="Follow").click()
    page.wait_for_function("() => { const r = document.querySelector(\".crs[data-id='21']\"); return r && r.textContent.includes('added by you'); }")
    assert (fui.root / "Intro to Programming Year 1").is_dir()
    wait_job(fui)
    go_files(fui, "all")
    assert page.locator(".pane.left .frow.lf", has_text="Old lecture.pdf").count() == 1
    finish(fui)


def test_focus_shows_one_folder_on_the_local_side_and_show_all_brings_the_rest_back(fui):
    go_files(fui)
    page, right = fui.page, fui.page.locator(".pane.right")
    notes = right.locator(".frow.dir", has_text="Notes").first
    assert notes.get_by_role("button", name="Focus").is_hidden()                  # only while you point at the folder
    notes.hover()
    notes.get_by_role("button", name="Focus").click()
    page.wait_for_selector("#unfocus")
    assert right.locator("header h2").inner_text() == "Notes" and right.locator(".frow.dir").count() == 1
    assert right.locator(".frow.rf", has_text="week1.md").is_visible() and right.locator(".frow", has_text="Lectures").count() == 0
    goto(fui, "")
    go_files(fui)                                                                 # kept while the tab is open
    assert page.locator("#unfocus").count() == 1
    page.locator("#unfocus").click()
    assert right.locator(".frow.rootrow").count() == 1 and right.locator(".frow.dir", has_text="Lectures").count() == 1
    finish(fui)
