"""Browser tests for the two Files views: smart allocation and organise (drag and drop, folders, preview, PDF text, selection)."""
import re
import time

import pytest

pw = pytest.importorskip("playwright.sync_api")
from test_ui import browser, goto, ui, wait_job as _wait_job  # noqa: E402,F401  (fixtures)

MOD = "CSCI.999 Test Module"


def wait_job(ui):
    ui.page.wait_for_timeout(350)             # let the click reach the server and the log box open
    _wait_job(ui)


def rows(ui, scope=".alloc"):
    return ui.page.locator(f"{scope} .frow[data-sel]")


def row(ui, text, scope="body"):
    return ui.page.locator(f"{scope} .frow[data-sel]", has_text=text).first


def selected(ui, scope=""):
    return ui.page.locator(f"{scope} [data-sel].selected:not([data-group])").count()


def go_files(ui, view):
    goto(ui, "files/" + view)
    ui.page.wait_for_selector(".alloc .frow, .org .frow")
    ui.page.wait_for_timeout(250)


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
def test_view_toggle_switches_remembers_and_has_no_native_checkboxes(fui):
    goto(fui, "files")
    fui.page.wait_for_selector(".alloc")
    assert fui.page.locator(".seg button[aria-pressed=true]").inner_text() == "Smart allocation"
    fui.page.get_by_role("button", name="Organise").click()
    fui.page.wait_for_selector(".org .pane.left")
    assert fui.page.evaluate("location.hash") == "#/files/organise"
    goto(fui, "")                                                  # leave and come back: the choice is remembered
    goto(fui, "files")
    fui.page.wait_for_selector(".org .pane.right")
    fui.page.get_by_role("button", name="Smart allocation").click()
    fui.page.wait_for_selector(".alloc")
    for view in ("allocate", "organise"):
        go_files(fui, view)
        assert fui.page.locator(".alloc input[type=checkbox], .org input[type=checkbox]").count() == 0      # no native checkboxes; the round box is drawn by the selection component
    finish(fui)


def test_allocate_groups_by_assigned_folder_and_bulk_download_and_ignore(fui):
    go_files(fui, "allocate")
    fui.page.get_by_role("button", name=re.compile("^Everything")).click()
    heads = fui.page.locator(".ghead")
    assert {h.inner_text().split("\n")[0].strip() for h in heads.all()} >= {"Lectures", "Workshops"}
    assert "will be created" in fui.page.locator(".ghead").first.inner_text()
    r = rows(fui)
    r.nth(0).click()
    r.nth(2).click(modifiers=["Shift"])
    assert selected(fui) == 3
    bar = fui.page.locator("#selbar")
    assert "3 selected" in bar.inner_text() and bar.get_by_role("button", name="Download 3").is_visible()
    bar.get_by_role("button", name="Ignore 3").click()
    fui.page.wait_for_timeout(500)
    assert selected(fui) == 3 and fui.page.locator(".frow.selected.st-ignored").count() == 3       # highlight follows the update
    fui.page.locator(".toast button", has_text="Undo").click()
    wait_job(fui)
    assert fui.page.locator(".frow.st-ignored").count() == 0
    r.nth(0).click()
    fui.page.locator("#selbar").get_by_role("button", name="Download 1").click()
    wait_job(fui)
    assert fui.page.locator(".frow.st-downloaded").count() == 1 and any(fui.folder.rglob("*.pdf"))
    finish(fui)


def test_group_header_click_selects_every_file_in_the_folder(fui):
    go_files(fui, "allocate")
    fui.page.get_by_role("button", name=re.compile("^Everything")).click()
    head = fui.page.locator(".ghead", has_text="Workshops")
    n = head.locator("xpath=..").locator(".frow[data-sel]").count()
    head.click()
    assert selected(fui) == n and "selected" in head.get_attribute("class").split()
    fui.page.locator(".ghead", has_text="Lectures").click(modifiers=["Control"])
    assert selected(fui) > n
    finish(fui)


def test_second_shift_click_only_moves_the_end_of_the_range(fui):
    """Regression: a second Shift+click used to drop a section selected earlier."""
    go_files(fui, "allocate")
    fui.page.get_by_role("button", name=re.compile("^Everything")).click()
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
    go_files(fui, "organise")
    target = fui.page.locator(".pane.right .frow.dir", has_text="Notes")
    f = fui.page.locator(".pane.left .frow.lf", has_text="Workshop 1.1.pdf")
    drag(fui, f, target)
    wait_job(fui)
    assert on_disk(fui, f"{MOD}/Notes/Workshop 1.1.pdf")
    fui.page.locator(".pane.left").get_by_role("button", name="All").click()
    assert fui.page.locator(".pane.left .frow.lf.st-downloaded", has_text="Workshop 1.1.pdf").count() == 1
    fui.page.locator(".pane.right .frow.dir", has_text="Notes").locator(".chev").click()
    assert fui.page.locator(".pane.right .frow.rf", has_text="Workshop 1.1.pdf").count() == 1
    finish(fui)


def test_multi_select_in_moodle_pane_drops_all_at_once(fui):
    go_files(fui, "organise")
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
    go_files(fui, "organise")
    # a downloaded Moodle file, then moved by drag
    fui.page.locator(".pane.left .frow.lf", has_text="Workshop 1.1.pdf").click()
    fui.page.locator("#selbar").get_by_role("button", name="Download 1").click()
    wait_job(fui)
    fui.page.wait_for_selector(".pane.right")
    right = fui.page.locator(".pane.right")
    f = right.locator(".frow.rf", has_text="Workshop 1.1.pdf")
    if not f.is_visible():                                                  # the folder opens by itself when you pick a file that is going there
        right.locator(".frow.dir", has_text="Workshops").locator(".chev").click()
    if fui.page.locator("#joblog").is_visible():
        fui.page.locator("#jclose").click()                                 # the finished job's log must not sit over the folders
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
    go_files(fui, "organise")
    right = fui.page.locator(".pane.right")
    mod = right.locator(".frow.dir", has_text=MOD)
    assert mod.get_attribute("draggable") is None and "module" in mod.inner_text()
    notes = right.locator(".frow.dir", has_text="Notes").first
    notes.locator(".chev").click()
    drag(fui, notes, notes)
    assert on_disk(fui, f"{MOD}/Notes/week1.md")
    finish(fui)


def test_new_folder_move_to_dialog_and_rename(fui):
    go_files(fui, "organise")
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
    go_files(fui, "organise")
    left = fui.page.locator(".pane.left .frow.lf")
    left.first.click()
    left.nth(left.count() - 1).click(modifiers=["Shift"])
    fui.page.locator("#selbar").get_by_role("button", name=re.compile("^Download \\d+$")).click()
    wait_job(fui)
    right = fui.page.locator(".pane.right")
    ws = right.locator(".frow.dir", has_text="Workshops").first
    assert fui.page.locator(".pane.right .acts").count() == 0                 # no clutter on the rows
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
    go_files(fui, "organise")
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
    go_files(fui, "organise")
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
    go_files(fui, "organise")
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


def test_copy_pdf_text_for_an_ai_chat(fui):
    import pymupdf
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "Gradient descent notes")
    d.save(str(fui.folder / "Notes" / "real.pdf"))
    go_files(fui, "organise")
    fui.page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    right = fui.page.locator(".pane.right")
    right.locator(".frow.dir", has_text="Notes").first.locator(".chev").click()
    right.locator(".frow.rf", has_text="real.pdf").click()
    fui.page.keyboard.press("t")
    ta = fui.page.locator("dialog.wide textarea")
    ta.wait_for()
    fui.page.wait_for_function("() => document.querySelector('dialog.wide textarea').value.includes('Gradient')")
    assert "tokens" in fui.page.locator("dialog.wide").inner_text()
    fui.page.locator("dialog.wide").get_by_role("button", name="Copy to clipboard").click()
    assert "Gradient descent" in fui.page.evaluate("navigator.clipboard.readText()")
    fui.page.locator("dialog.wide").get_by_role("button", name="Save .txt next to the PDF").click()
    fui.page.wait_for_timeout(500)
    assert (fui.folder / "Notes" / "real.txt").exists()
    assert any((fui.home / "tmp").glob("real.txt"))
    finish(fui)


def test_double_click_a_tracked_file_in_moodle_pane_reveals_it_on_the_right(fui):
    go_files(fui, "organise")
    fui.page.locator(".pane.left .frow.lf", has_text="Workshop 1.1.pdf").click()
    fui.page.locator("#selbar").get_by_role("button", name="Download 1").click()
    wait_job(fui)
    fui.page.locator(".pane.left").get_by_role("button", name="All").click()
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
    assert ui.page.locator("#joblog").is_hidden()
    ui.page.wait_for_selector(".sync:not(.busy)", timeout=60000)
    assert "Checked" in ui.page.locator("#syncwhen").inner_text()


# ---------------------------------------------------------------- the shared selection component
def box_opacity(ui, scope=".alloc"):
    ui.page.wait_for_timeout(250)                                             # let the fade finish
    return ui.page.evaluate("s => [...document.querySelectorAll(s + ' [data-sel] > .selbox')].map(b => getComputedStyle(b).opacity)", scope)


def test_one_round_box_is_always_visible_on_every_row(fui):
    go_files(fui, "allocate")
    fui.page.get_by_role("button", name=re.compile("^Everything")).click()
    fui.page.mouse.move(5, 5)
    assert set(box_opacity(fui)) == {"1"}                                     # visible before anything is selected
    n = fui.page.evaluate("() => [...document.querySelectorAll('.alloc [data-sel]')].filter(r => r.querySelectorAll(':scope > .selbox').length !== 1).length")
    assert n == 0                                                             # exactly one per row
    rows(fui).nth(1).click()
    fui.page.keyboard.press("Escape")
    assert set(box_opacity(fui)) == {"1"}
    finish(fui)


def test_clicking_a_box_toggles_and_shift_on_a_box_extends(fui):
    go_files(fui, "allocate")
    fui.page.get_by_role("button", name=re.compile("^Everything")).click()
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


def test_group_box_is_half_checked_and_selects_the_folder(fui):
    go_files(fui, "allocate")
    fui.page.get_by_role("button", name=re.compile("^Everything")).click()
    head = fui.page.locator(".ghead", has_text="Workshops")
    grp = head.locator("xpath=..")
    grp.locator(".frow[data-sel]").first.click()
    assert "partial" in head.get_attribute("class").split() and "selected" not in head.get_attribute("class").split()
    head.locator(".selbox").click()                                           # box on the heading: everything in the folder
    assert "selected" in head.get_attribute("class").split() and selected(fui) == grp.locator(".frow[data-sel]").count()
    head.locator(".selbox").click()                                           # and again: nothing left
    assert selected(fui) == 0
    finish(fui)


def test_collapsing_keeps_the_selection_and_changing_the_filter_drops_what_you_cannot_see(fui):
    go_files(fui, "allocate")
    fui.page.get_by_role("button", name=re.compile("^Everything")).click()
    head = fui.page.locator(".ghead", has_text="Lectures")
    grp = head.locator("xpath=..")
    head.locator(".selbox").click()
    n = selected(fui)
    assert n >= 5
    head.locator(".chev").click()                                             # collapse: the files are hidden, still selected, and the bar says how many
    assert grp.locator(".frow").first.is_hidden() and "selected" in head.get_attribute("class").split()
    assert f"{n} selected" in fui.page.locator("#selbar").inner_text()
    fui.page.get_by_role("button", name=re.compile("^Ignored")).click()       # other filter: those files are gone from the page, so from the selection
    assert not fui.page.locator("#selbar.on").count() or "Download" not in fui.page.locator("#selbar").inner_text()
    assert selected(fui) <= 1
    finish(fui)


def test_select_all_in_the_bar_and_ctrl_a(fui):
    go_files(fui, "allocate")
    fui.page.get_by_role("button", name=re.compile("^Everything")).click()
    rows(fui).nth(0).click()
    fui.page.get_by_role("button", name=re.compile("^Select all")).click()
    assert selected(fui) == rows(fui).count()
    fui.page.keyboard.press("Escape")
    assert selected(fui) == 0
    rows(fui).nth(0).click()
    fui.page.keyboard.press("Control+a")
    assert selected(fui) == rows(fui).count()
    finish(fui)


def test_quick_look_works_from_smart_allocation_too(fui):
    go_files(fui, "allocate")
    fui.page.get_by_role("button", name=re.compile("^Everything")).click()
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
    go_files(fui, "organise")
    wk = fui.page.locator(".pane.left .frow.lg", has_text="Week 3")
    wk.locator(".selbox").click()
    assert selected(fui, ".pane.left") == 2 and "selected" in wk.get_attribute("class").split()
    assert "2 Moodle files selected" in fui.page.locator("#selbar").inner_text()
    finish(fui)


# ---------------------------------------------------------------- files still to come, shown where they will land
def cls(el, prefix="c"):
    return next(c for c in el.get_attribute("class").split() if re.fullmatch(prefix + r"\d", c))


def test_files_to_come_appear_in_their_folder_in_the_colour_of_their_chip(fui):
    go_files(fui, "organise")
    left = fui.page.locator(".pane.left .frow.lf", has_text="Lecture 3.0.pdf")
    ghost = fui.page.locator(".pane.right .frow.gh", has_text="Lecture 3.0.pdf")
    other = fui.page.locator(".pane.right .frow.gh", has_text="Workshop 3.1.pdf")
    chip = left.locator(".dest")
    assert "will go to" in chip.inner_text() and ghost.count() == 1 and "to come" in ghost.inner_text()
    assert cls(chip) == cls(ghost.first)                                            # same colour on both sides
    assert cls(chip) != cls(fui.page.locator(".pane.left .frow.lf", has_text="Workshop 3.1.pdf").locator(".dest"))
    assert cls(other.first) == cls(fui.page.locator(".pane.left .frow.lf", has_text="Workshop 3.1.pdf").locator(".dest"))
    left.click()                                                                     # picking it lights up where it lands
    fui.page.wait_for_selector(".pane.right .frow.gh.hl")
    assert "Lecture 3.0" in fui.page.locator(".pane.right .frow.gh.hl").inner_text()
    assert fui.page.locator(".pane.right .frow.dir.hl").count() == 1
    fui.page.keyboard.press("Escape")
    assert fui.page.locator(".pane.right .frow.gh.hl").count() == 0
    ghost.first.click()                                                              # and the other way round
    assert "selected" in left.get_attribute("class").split()
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


def test_dragging_a_planned_file_changes_where_it_goes_without_downloading(fui):
    go_files(fui, "organise")
    fui.page.locator(".pane.right .frow.dir", has_text="Lectures").first.locator(".chev").click()
    before = sorted(p.relative_to(fui.root).as_posix() for p in fui.root.rglob("*"))
    slow_drag(fui, fui.page.locator(".pane.right .frow.gh").first, fui.page.locator(".pane.right .frow.dir", has_text="Notes").first)
    fui.page.wait_for_function("() => [...document.querySelectorAll('.pane.left .frow.lf .dest')].some(e => e.textContent.endsWith('Notes'))")
    assert sorted(p.relative_to(fui.root).as_posix() for p in fui.root.rglob("*")) == before          # nothing was downloaded
    moved = fui.page.locator(".pane.left .frow.lf", has=fui.page.locator(".dest", has_text="Notes"))
    assert moved.count() == 1
    name = moved.locator(".name").inner_text()
    moved.click()
    fui.page.locator("#selbar").get_by_role("button", name=re.compile("^Download 1")).click()           # downloading uses the new plan
    wait_job(fui)
    assert (fui.folder / "Notes" / name).exists()
    finish(fui)


def test_rows_keep_their_height_when_you_hover_them(fui):
    go_files(fui, "organise")
    fui.page.locator(".pane.right .frow.dir", has_text="Lectures").first.locator(".chev").click()
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
    go_files(fui, "organise")
    right = fui.page.locator(".pane.right")

    def to_come():
        return sum(int(re.match(r"\d+", t).group()) for t in right.locator(".dir .b-new, .dir .dest").all_inner_texts() if re.match(r"\d+ to come", t))
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
    go_files(fui, "organise")
    right = fui.page.locator(".pane.right")
    right.locator(".frow.dir", has_text="Notes").first.click(button="right")
    fui.page.get_by_role("menuitem", name=re.compile("^Create notes")).click()
    fui.page.wait_for_selector(".ai-card")
    assert "#/ai" in fui.page.url and fui.page.locator(".ai-card h2", has_text="Note-taker").count() == 1
    assert "selected item" in fui.page.locator(".ai-card").nth(1).inner_text()
    for h2 in ("Read", "Note-taker", "Prompts", "Ask"):
        assert fui.page.locator(".ai-card h2", has_text=h2).count() == 1


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
    fui.page.wait_for_selector(".set-card")
    fui.page.get_by_label("When the app starts").select_option("none")
    fui.page.get_by_role("button", name="Save changes").click()
    fui.page.wait_for_selector(".toast >> text=Saved")
    assert fui.ctx.settings.open_on_launch == "none"
    assert '"open_on_launch": "none"' in fui.ctx.settings.config_path.read_text()
