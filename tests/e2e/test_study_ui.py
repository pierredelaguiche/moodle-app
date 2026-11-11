"""Browser tests for the Study page: module files, tiles, the file creator and the assistant (a fake `claude` command answers)."""
import json
from urllib.parse import quote

import pytest

pw = pytest.importorskip("playwright.sync_api")
from test_ui import browser, goto, ui  # noqa: E402,F401  (fixtures/helpers)

MOD = "CSCI.999 Test Module"


def open_file(ui, name):
    goto(ui, "edit/" + quote(f"{MOD}/Notes/{name}", safe=""))
    ui.page.wait_for_selector(f".edtab >> text={name}")


def fake_model(ui, tmp_path, answer):
    out, fake = tmp_path / "o.json", tmp_path / "claude"
    out.write_text(json.dumps({"result": answer, "is_error": False, "stop_reason": "end_turn", "usage": {"input_tokens": 30, "output_tokens": 9}}))
    fake.write_text(f"#!/bin/sh\ncat > {tmp_path}/stdin.txt\ncat {out}\n")
    fake.chmod(0o755)
    ui.ctx.settings.llm = {"provider": "claude_cli", "model": "haiku", "executable": str(fake)}
    return tmp_path / "stdin.txt"


def clean(ui):
    ui.page.wait_for_timeout(300)
    assert [e for e in ui.page.errors if e[0] in ("pageerror", "error")] == [], ui.page.errors


def test_study_replaces_the_editor_in_the_menu_and_lists_the_module_files(ui):
    names = ui.page.locator("#nav .navitem[data-nav]").all_inner_texts()
    assert any(n.startswith("Study") for n in names) and not any(n.startswith("Editor") for n in names)
    goto(ui, "study")
    ui.page.wait_for_selector(".st-file")
    assert ui.page.locator(".st-side select").input_value() == MOD
    assert sorted(ui.page.locator(".st-file .name").all_inner_texts()) == ["sheet.pdf", "todo.txt", "week1.md"]
    assert "Nothing open" in ui.page.locator(".st-tiles").inner_text()
    ui.page.locator(".st-side input[type=search]").fill("week")
    assert ui.page.locator(".st-file .name").all_inner_texts() == ["week1.md"]
    clean(ui)


def test_tiles_open_side_by_side_up_to_four_and_markdown_reads_typeset(ui):
    for n in ("a.md", "b.md"):
        (ui.folder / "Notes" / n).write_text(f"# {n}\n\ntext of {n}\n")
    goto(ui, "study")
    ui.page.wait_for_selector(".st-file")
    page = ui.page
    page.locator(".st-file", has_text="week1.md").click()
    page.wait_for_selector(".tile")
    assert page.locator(".tile").count() == 1 and page.locator(".edbody.split textarea").count() == 1      # alone: text and preview
    page.locator(".st-file", has_text="todo.txt").click()                    # a plain click opens it as a tab of the same section
    page.wait_for_selector(".sec-tab.on >> text=todo.txt")
    assert page.locator(".tile").count() == 1 and page.locator(".sec-tab .name").all_inner_texts() == ["week1.md", "todo.txt"]
    for name in ("week1.md", "a.md", "b.md"):
        page.locator(".st-file", has_text=name).click(modifiers=["Alt"])     # Alt-click: a section of its own
        page.wait_for_selector(f".edtab >> text={name}")
    assert page.locator(".tile").count() == 4 and page.locator(".st-tiles").get_attribute("data-n") == "4"
    wk = page.locator(".tile", has_text="week1.md")
    assert wk.locator(".md h1").count() == 1 and wk.locator("textarea").count() == 0        # among others a note opens to read
    wk.get_by_role("button", name="Edit").click()
    assert page.locator(".tile", has_text="week1.md").locator("textarea").count() == 1
    assert page.locator(".st-file.open").count() == 4
    page.locator(".tile", has_text="b.md").locator(".x").click()
    page.wait_for_function("() => document.querySelectorAll('.tile').length === 3")
    goto(ui, "")                                                             # leave and come back: the tiles are still there
    goto(ui, "study")
    page.wait_for_selector(".tile")
    assert page.locator(".tile").count() == 3
    clean(ui)


def tiles_layout(page):
    """[[x, y, w, h, 'visible file'], ...] of the sections, rounded, in reading order."""
    return page.evaluate("""() => [...document.querySelectorAll('.tile')].map(t => { const r = t.getBoundingClientRect();
        return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height), t.querySelector('.sec-tab.on .name').textContent]; })""")


def drag_to_zone(page, src, zone):
    """Drag a file row or a tab onto one of the four drop zones (0 top left, 1 top right, 2 bottom left, 3 bottom right)."""
    a = src.bounding_box()
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    page.mouse.down()
    page.mouse.move(a["x"] + a["width"] / 2 + 12, a["y"] + a["height"] / 2 + 12, steps=3)
    page.wait_for_selector(".study.arranging .st-drop .zone")
    z = page.locator(f".st-drop .zone[data-zone='{zone}']").bounding_box()
    page.mouse.move(z["x"] + z["width"] / 2, z["y"] + z["height"] / 2, steps=12)
    page.mouse.up()
    page.wait_for_function("() => !document.querySelector('.study.arranging')")
    page.wait_for_timeout(250)


def test_four_sections_are_arranged_by_dragging_files_and_tabs(ui):
    for n in ("a.md", "b.md", "c.md"):
        (ui.folder / "Notes" / n).write_text(f"# {n}\n")
    goto(ui, "study")
    page = ui.page
    page.wait_for_selector(".st-file")
    page.locator(".st-file", has_text="week1.md").click()                    # one file: the whole area
    page.wait_for_selector(".tile")
    full = tiles_layout(page)[0]
    drag_to_zone(page, page.locator(".st-file", has_text="a.md"), 3)         # a file from the list -> bottom right
    lay = tiles_layout(page)
    assert [t[4] for t in lay] == ["week1.md", "a.md"] and lay[1][1] > lay[0][1] and lay[0][2] == lay[1][2] == full[2]      # two rows, full width each
    drag_to_zone(page, page.locator(".st-file", has_text="b.md"), 1)         # top right: the top row is shared, the bottom one still full width
    lay = {t[4]: t for t in tiles_layout(page)}
    assert lay["b.md"][0] > lay["week1.md"][0] and lay["b.md"][1] == lay["week1.md"][1] and lay["a.md"][2] > lay["b.md"][2] * 1.8
    drag_to_zone(page, page.locator(".st-file", has_text="c.md"), 2)
    lay = {t[4]: t for t in tiles_layout(page)}
    assert len(lay) == 4 and lay["c.md"][0] < lay["a.md"][0] and lay["c.md"][1] == lay["a.md"][1] > lay["week1.md"][1]     # the 2 x 2 grid
    assert page.locator(".st-tiles").get_attribute("data-n") == "4"
    drag_to_zone(page, page.locator(".sec-tab", has_text="b.md"), 2)         # a tab onto another section: it joins that section's tabs
    assert page.locator(".tile").count() == 3
    bottom_left = page.locator(".tile[data-i='2']")
    assert bottom_left.locator(".sec-tab .name").all_inner_texts() == ["c.md", "b.md"] and bottom_left.locator(".sec-tab.on").inner_text().startswith("b.md")
    bottom_left.locator(".sec-tab", has_text="c.md").click()                 # tabs switch what the section shows
    assert bottom_left.locator(".sec-tab.on .name").inner_text() == "c.md" and "c.md" in bottom_left.locator(".md h1").inner_text()
    assert "On screen (3)" in page.locator(".st-ctx").inner_text() and "b.md" not in page.locator(".st-ctx").inner_text()   # the assistant gets what you see
    bottom_left.locator(".sec-tab", has_text="b.md").click(button="right")   # the same moves without dragging
    page.get_by_role("menuitem", name="Move to top right").click()
    page.wait_for_function("() => document.querySelectorAll('.tile').length === 4")
    assert page.locator(".tile[data-i='1'] .sec-tab.on .name").inner_text() == "b.md"
    goto(ui, "")                                                             # the arrangement survives leaving the page
    goto(ui, "study")
    page.wait_for_selector(".tile")
    assert {t[4] for t in tiles_layout(page)} == {"week1.md", "a.md", "b.md", "c.md"} and page.locator(".tile[data-i='3'] .sec-tab.on .name").inner_text() == "a.md"
    clean(ui)


def test_sections_resize_with_the_bars_between_them_and_tabs_close(ui):
    for n in ("a.md", "b.md"):
        (ui.folder / "Notes" / n).write_text(f"# {n}\n")
    goto(ui, "study")
    page = ui.page
    page.wait_for_selector(".st-file")
    page.locator(".st-file", has_text="week1.md").click()
    page.wait_for_selector(".tile")
    page.locator(".st-file", has_text="a.md").hover()
    page.locator(".st-file", has_text="a.md").locator(".side").click()
    page.wait_for_function("() => document.querySelectorAll('.tile').length === 2")
    before = tiles_layout(page)
    assert abs(before[0][2] - before[1][2]) <= 2                             # equal halves to begin with
    bar = page.locator(".st-split.v").bounding_box()
    page.mouse.move(bar["x"] + 4, bar["y"] + 60)
    page.mouse.down()
    page.mouse.move(bar["x"] + 4 + 160, bar["y"] + 60, steps=8)
    page.mouse.up()
    after = tiles_layout(page)
    assert after[0][2] > before[0][2] + 120 and after[1][2] < before[1][2] - 120
    page.locator(".st-file", has_text="b.md").click(modifiers=["Alt"])       # a third section: a second row appears, with its own bar
    page.wait_for_function("() => document.querySelectorAll('.tile').length === 3")
    assert page.locator(".st-split.h").count() == 1 and abs(tiles_layout(page)[0][2] - after[0][2]) <= 2      # the width you set is kept
    page.locator(".st-split.v").dblclick()                                   # double-click: equal halves again
    lay = tiles_layout(page)
    assert abs(lay[0][2] - lay[1][2]) <= 2
    page.locator(".tile[data-i='2'] .sec-tab .x").click()                    # closing the only tab gives the room back
    page.wait_for_function("() => document.querySelectorAll('.tile').length === 2 && !document.querySelector('.st-split.h')")
    clean(ui)


def test_file_creator_makes_typed_files_in_the_module(ui):
    goto(ui, "study")
    ui.page.wait_for_selector(".st-file")
    ui.page.locator("#newfile").click()
    dlg = ui.page.locator("dialog.creator")
    dlg.get_by_label("Name").fill("lab 2")
    dlg.get_by_label("Type").select_option("py")
    dlg.get_by_role("button", name="Create").click()
    ui.page.wait_for_selector(".edtab >> text=lab 2.py")
    made = ui.folder / "Notes" / "lab 2.py"
    assert made.exists() and "def main()" in made.read_text()
    assert "lab 2.py" in ui.page.locator(".st-file.open .name").all_inner_texts()
    ui.page.locator("#newfile").click()                                      # the same name again is refused, nothing is overwritten
    dlg = ui.page.locator("dialog.creator")
    dlg.get_by_label("Name").fill("lab 2")
    dlg.get_by_label("Type").select_option("py")
    dlg.get_by_role("button", name="Create").click()
    ui.page.wait_for_selector(".toast.err")
    assert "def main()" in made.read_text()


def test_assistant_gets_the_open_file_with_unsaved_edits_plus_what_you_hand_it(ui, tmp_path):
    sent = fake_model(ui, tmp_path, "## Tidy\n\n- point one")
    page = ui.page
    open_file(ui, "todo.txt")
    ta = page.locator(".edbody textarea")
    ta.click()
    page.keyboard.press("Control+End")
    page.keyboard.type("UNSAVED-LINE")
    assert "On screen (1)" in page.locator(".st-ctx").inner_text() and "todo.txt" in page.locator(".st-ctx").inner_text()
    ta.evaluate("t => t.setSelectionRange(0, 3)")                            # select "one" and hand it over
    page.get_by_role("button", name="+ Selection").click()
    page.get_by_role("button", name="+ Text").click()
    page.locator("dialog textarea").fill("Exercise 4: prove it.")
    page.locator("dialog").get_by_role("button", name="Add").click()
    page.get_by_role("button", name="+ File").click()
    page.locator("dialog select").select_option(f"{MOD}/Notes/week1.md")
    page.locator("dialog").get_by_role("button", name="Add").click()
    page.wait_for_function("() => document.querySelectorAll('.st-ctx .chip.ctx').length === 4")      # the open file + selection + pasted text + added file
    page.locator(".st-ask textarea").fill("Tidy this up")
    page.locator(".st-ask").get_by_role("button", name="Send").click()
    page.wait_for_selector(".msg.ai .md li")
    prompt = sent.read_text()
    for piece in ("UNSAVED-LINE", "with unsaved edits", "Exercise 4: prove it.", "read **slides**", "Selection from todo.txt", "Student: Tidy this up"):
        assert piece in prompt, piece
    assert (ui.folder / "Notes" / "todo.txt").read_text() == "one\ntwo\n"   # asking saved nothing
    page.locator(".st-ask textarea").fill("And shorter?")                     # a follow-up carries the conversation
    page.keyboard.press("Control+Enter")
    page.wait_for_function("() => document.querySelectorAll('.msg.ai:not(.wait)').length === 2")
    assert "Assistant: ## Tidy" in sent.read_text() and sent.read_text().rstrip().endswith("Student: And shorter?")
    clean(ui)


def test_assistant_answers_can_be_inserted_replaced_or_saved_as_a_note(ui, tmp_path):
    fake_model(ui, tmp_path, "REVISED NOTES")
    page = ui.page
    open_file(ui, "todo.txt")
    page.locator(".st-ask textarea").fill("Rewrite")
    page.locator(".st-ask").get_by_role("button", name="Send").click()
    page.wait_for_selector(".msg.ai .macts")
    page.locator(".edbody textarea").click()
    page.keyboard.press("Control+End")
    page.locator(".msg.ai").get_by_role("button", name="Insert").click()
    assert page.locator(".edbody textarea").input_value() == "one\ntwo\nREVISED NOTES\n"
    assert (ui.folder / "Notes" / "todo.txt").read_text() == "one\ntwo\n"   # only in the tile until you save
    page.locator(".msg.ai").get_by_role("button", name="Replace file").click()
    page.locator("dialog").get_by_role("button", name="Replace").click()
    page.wait_for_function("() => document.querySelector('.edbody textarea').value === 'REVISED NOTES'")
    page.locator(".tile").get_by_role("button", name="Revert").click()       # and Revert brings the saved text back
    assert page.locator(".edbody textarea").input_value() == "one\ntwo\n"
    page.locator(".msg.ai").get_by_role("button", name="Save as note").click()
    dlg = page.locator("dialog.creator")
    dlg.get_by_label("Name").fill("answer")
    dlg.get_by_role("button", name="Create").click()
    page.wait_for_selector(".edtab >> text=answer.md")
    assert (ui.folder / "Notes" / "answer.md").read_text() == "REVISED NOTES\n" and page.locator(".tile").count() == 2
    clean(ui)


def test_assistant_can_be_hidden_and_says_how_to_connect_a_model(ui):
    page = ui.page
    ui.ctx.settings.llm = {"provider": "claude_cli", "model": "haiku", "executable": "/nonexistent/claude"}
    open_file(ui, "todo.txt")
    page.locator(".st-ask textarea").fill("hello")
    page.locator(".st-ask").get_by_role("button", name="Send").click()
    page.wait_for_selector(".msg.ai.err")
    assert "Settings" in page.locator(".msg.ai.err").inner_text()
    page.locator("#aitoggle").click()
    assert page.locator(".st-ai").is_hidden() and page.locator(".tile").is_visible()
    page.locator("#aitoggle").click()
    assert page.locator(".st-ask textarea").is_visible()
