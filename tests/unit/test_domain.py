from pathlib import Path

import pytest

from moodle_app.config import DEFAULT_CATEGORIES
from moodle_app.domain.classify import classify
from moodle_app.domain.duplicates import find_duplicate, find_moved, match_folder
from moodle_app.domain.weeks import is_divided, week_dir, week_name, week_of_path


@pytest.mark.parametrize("fname,mod,sec,expect", [
    ("Workshop 3.pdf", "Resource", "Week 3", "Workshops"),
    ("00 intro.pdf", "x", "y", "Lectures"),
    ("notes.pdf", "Past Exam Papers", "Revision", "Exams"),
    ("lecture 2 slides.pdf", "x", "Workshop section", "Lectures"),     # file name beats section name
    ("misc.pdf", "Misc", "Misc", "Other"),
    ("Module Handbook.pdf", "x", "y", "."),
    ("labs.pdf", "x", "y", "Workshops"),
    ("tabulation.pdf", "x", "y", "Other"),                             # 'lab' inside a word must not match
])
def test_classify(fname, mod, sec, expect):
    assert classify(DEFAULT_CATEGORIES, fname, mod, sec) == expect


def test_find_duplicate_priority(tmp_path):
    a, b, c = tmp_path / "a" / "Same Stem.pdf", tmp_path / "b" / "other name.pdf", tmp_path / "c" / "wanted.pdf"
    for p, n in ((a, 100), (b, 20000), (c, 5)):
        p.parent.mkdir()
        p.write_bytes(b"x" * n)
    idx = {100: [a], 20000: [b], 5: [c]}
    assert find_duplicate(idx, set(), "same_stem.pdf", 100) == a
    assert find_duplicate(idx, set(), "WANTED.PDF", 999) == c
    assert find_duplicate(idx, set(), "new.pdf", 20000) == b
    assert find_duplicate(idx, set(), "new.pdf", 100) is None
    assert find_duplicate(idx, {str(a)}, "same_stem.pdf", 100) is None


def test_find_moved(tmp_path):
    a = tmp_path / "x" / "Doc.pdf"
    a.parent.mkdir()
    a.write_bytes(b"x" * 20000)
    assert find_moved({20000: [a]}, set(), tmp_path / "old" / "Doc.pdf", 20000) == a
    assert find_moved({20000: [a]}, set(), tmp_path / "old" / "Other.pdf", 20000) == a      # single big candidate
    assert find_moved({100: [a]}, set(), tmp_path / "old" / "Other.pdf", 100) is None       # small + different name


def test_week_dir_reuses_existing(tmp_path):
    (tmp_path / "Workshops" / "week_02").mkdir(parents=True)
    assert week_dir(tmp_path, "Workshops", 2).name == "week_02"
    assert week_dir(tmp_path, "Workshops", 3).name == "week_03"            # a new week folder is named like its siblings
    assert week_dir(tmp_path, "Lectures", 3).name == "Week 03"              # nothing to follow: the default name
    assert week_of_path(("Lectures", "Week 04", "x.pdf")) == 4 and week_of_path(("a", "b")) is None


def test_match_folder_by_module_code(tmp_path):
    f = tmp_path / "CSCI.323 Deep Learning"
    assert match_folder({"shortname": "CSCI.323", "fullname": "x"}, [f]) == f
    assert match_folder({"shortname": "CSCI.999", "fullname": "x"}, [f]) is None


def test_week_for_prefers_names_over_section_index():
    from moodle_app.domain.weeks import week_for
    assert week_for(1, "Week 3: Runtime", "x.pdf") == (3, "section name")
    assert week_for(1, "Week 1", "w02Lab.pdf")[0] == 2
    assert week_for(3, "Misc", "w02Lab00Sol.pdf") == (2, "file name")
    assert week_for(1, "Intro", "week2 solutions.zip")[0] == 2
    assert week_for(1, "Intro", "03_RuntimeBehavior.pdf", lead=True) == (3, "lecture number")
    assert week_for(1, "Intro", "03_RuntimeBehavior.pdf", lead=False)[0] == 1
    assert week_for(4, "Block", "notes.pdf") == (4, "section index")
    assert week_for(0, "General", "00 Welcome.pdf", lead=True)[0] == 0
    assert week_for(2, "", "w3c-spec.pdf")[0] == 2


def test_week_name_follows_the_existing_style():
    assert week_name([], 4) == "Week 04"
    assert week_name(["Week 1", "Week 2"], 10) == "Week 10" and week_name(["Week 1", "Week 2"], 3) == "Week 3"
    assert week_name(["week_01", "week_02"], 3) == "week_03"
    assert week_name(["WEEK-1"], 12) == "WEEK-12"
    assert week_name(["Week 01", "week3"], 5) in ("Week 05", "week5")      # mixed: either sibling style is acceptable


def test_is_divided_needs_a_real_week_folder(tmp_path):
    (tmp_path / "Lectures").mkdir()
    (tmp_path / "Lectures" / "Weekly notes").mkdir()
    (tmp_path / "Lectures" / "Week 2.pdf").write_text("x")
    assert not is_divided(tmp_path / "Lectures") and not is_divided(tmp_path / "Missing")
    (tmp_path / "Lectures" / "Week 2").mkdir()
    assert is_divided(tmp_path / "Lectures")
