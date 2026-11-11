"""Following more courses than the folder names match: earlier years, courses without a module code, courses you are not enrolled in."""
import pytest

from helpers import PDF
from test_web import req, wait_job, web  # noqa: F401  (fixture and helpers)


def by_id(web):  # noqa: F811
    return {c["id"]: c for c in req(web, "/api/courses")[1]["courses"]}


def test_enrolled_course_without_a_matching_folder_is_listed_and_can_be_followed(web):  # noqa: F811
    web.mock.courses.append({"id": 21, "fullname": "Introduction to Programming (Year 1)", "shortname": "Y1 Programming"})
    web.mock.sections[21] = []
    web.mock.add_file(21, 1, 900, "Old lecture.pdf", PDF + b"o")
    web.app.refresh()
    c = by_id(web)
    assert c[7]["tracked"] and not c[7]["added"] and c[21]["tracked"] is False and c[21]["folder"] == ""
    assert req(web, "/api/courses", token=False)[0] == 403
    s, r = req(web, "/api/courses/add", {"ref": "21", "folder": "Y1 Programming"})
    assert s == 200 and r["folder"] == "Y1 Programming" and (web.root / "Y1 Programming").is_dir()
    wait_job(web)
    c = by_id(web)
    assert c[21]["tracked"] and c[21]["added"] and c[21]["folder"] == "Y1 Programming"
    files = req(web, "/api/alloc")[1]["files"]
    assert any(f["name"] == "Old lecture.pdf" and f["folder"].startswith("Y1 Programming") for f in files)
    web.app.refresh()                                                    # the choice survives later checks
    assert by_id(web)[21]["tracked"]


def test_course_you_are_not_enrolled_in_is_added_by_its_address(web):  # noqa: F811
    web.mock.open_courses[50260] = {"id": 50260, "fullname": "Careers: Open Resources", "shortname": "CAREERS"}
    web.mock.sections[50260] = []
    web.mock.add_file(50260, 0, 950, "Handbook.pdf", PDF + b"h")
    s, r = req(web, "/api/courses/add", {"ref": web.mock.base + "/course/view.php?id=50260"})
    assert s == 200 and r["name"] == "Careers: Open Resources" and r["folder"] == "Careers Open Resources"
    wait_job(web)
    assert by_id(web)[50260]["tracked"] and (web.root / "Careers Open Resources").is_dir()
    assert any(f["name"] == "Handbook.pdf" for f in req(web, "/api/alloc")[1]["files"])
    assert req(web, "/api/courses/remove", {"id": 50260})[0] == 200      # stop following: folder stays, course drops out
    assert by_id(web)[50260]["tracked"] is False and (web.root / "Careers Open Resources").is_dir()
    web.app.refresh()
    assert 50260 not in by_id(web) or by_id(web)[50260]["tracked"] is False


@pytest.mark.parametrize("body, msg", [({"ref": "not a course"}, "number or the address"), ({"ref": "424242"}, "no course 424242"),
                                       ({"ref": "7", "folder": "../up"}, "not a usable"), ({"ref": "7", "folder": "_hidden"}, "underscore")])
def test_bad_requests_are_refused_with_a_reason(web, body, msg):  # noqa: F811
    s, r = req(web, "/api/courses/add", body)
    assert s == 400 and msg in r["error"], r


def test_a_folder_cannot_serve_two_courses_and_matched_courses_are_not_removable(web):  # noqa: F811
    web.mock.courses.append({"id": 22, "fullname": "Another", "shortname": "ANOTHER"})
    web.mock.sections[22] = []
    web.app.refresh()
    s, r = req(web, "/api/courses/add", {"ref": "22", "folder": "CSCI.999 Test Module"})
    assert s == 400 and "already belongs" in r["error"]
    s, r = req(web, "/api/courses/remove", {"id": 7})
    assert s == 400 and "matched by its folder name" in r["error"]


# ---------------------------------------------------------------- year folders (Y1/Y2/Y3), like week folders one level up
def test_year_helpers(tmp_path):
    from moodle_app.domain import years
    for d in ("Year 1", "Year 1/MATH.101 Calculus", "Year 3", "Loose module", "_old", "Yearbook"):
        (tmp_path / d).mkdir(parents=True)
    assert years.module_dirs(tmp_path) == ["Loose module", "Year 1/MATH.101 Calculus", "Yearbook"]
    assert years.year_folder(tmp_path, 3) == "Year 3" and years.year_folder(tmp_path, 2) == "Year 2"      # named like its siblings
    assert years.year_name([], 2) == "Y2"
    assert years.year_of({"shortname": "CSCI.323"}) == 3 and years.year_of({"fullname": "MATH101 Calculus 2024"}) == 1
    y0 = years.academic_year(1_727_000_000)                                                                # Sep 2024
    assert years.year_of({"shortname": "Careers", "startdate": 1_727_000_000 + 370 * 86400}, y0) == 2
    assert years.year_of({"shortname": "Careers"}) is None


def test_a_module_folder_inside_a_year_folder_is_matched_and_its_files_land_there(web):  # noqa: F811
    web.mock.courses.append({"id": 31, "fullname": "MATH.101 Calculus", "shortname": "MATH.101"})
    web.mock.sections[31] = []
    web.mock.add_file(31, 1, 700, "Lecture 1.pdf", PDF + b"m")
    (web.root / "Y1" / "MATH.101 Calculus").mkdir(parents=True)
    web.app.refresh()
    c = by_id(web)
    assert c[31]["tracked"] and c[31]["folder"] == "Y1/MATH.101 Calculus" and not c[31]["added"]
    f = next(f for f in req(web, "/api/alloc")[1]["files"] if f["name"] == "Lecture 1.pdf")
    assert f["folder"] == "Y1/MATH.101 Calculus/Lectures"
    s, j = req(web, "/api/download", {"keys": [f["key"]]})
    wait_job(web)
    assert (web.root / "Y1" / "MATH.101 Calculus" / "Lectures" / "Lecture 1.pdf").exists()
    assert "Y1/MATH.101 Calculus" in web.app.ai.modules() and "Y1" not in web.app.ai.modules()
    assert req(web, "/api/fs/rename", {"rel": "Y1", "name": "Year1"})[0] == 400              # a year folder that holds modules stays put


def test_courses_are_offered_their_year_folder_and_follow_all_puts_each_in_its_year(web):  # noqa: F811
    web.mock.courses += [{"id": 41, "fullname": "MATH.101 Calculus", "shortname": "MATH.101"},
                         {"id": 42, "fullname": "CSCI.210: Algorithms", "shortname": "CSCI.210"},
                         {"id": 43, "fullname": "Careers", "shortname": "CAREERS"}]
    for i in (41, 42, 43):
        web.mock.sections[i] = []
    web.app.refresh()
    r = req(web, "/api/courses")[1]
    assert r["years"] is False and by_id(web)[41]["suggest"] == "MATH.101 Calculus"            # not divided by years: plain folders
    (web.root / "Y3").mkdir()
    r = req(web, "/api/courses")[1]
    c = {x["id"]: x for x in r["courses"]}
    assert r["years"] is True and c[41]["suggest"] == "Y1/MATH.101 Calculus" and c[42]["suggest"] == "Y2/CSCI.210 Algorithms"
    assert c[43]["year"] is None and c[43]["suggest"] == "Careers"                             # no code, no date: left for you to place
    s, out = req(web, "/api/courses/add-all", {})
    assert s == 200 and len(out["added"]) == 3 and out["failed"] == []
    wait_job(web)
    c = by_id(web)
    assert c[41]["folder"] == "Y1/MATH.101 Calculus" and c[42]["folder"] == "Y2/CSCI.210 Algorithms" and c[43]["folder"] == "Careers"
    assert (web.root / "Y2" / "CSCI.210 Algorithms").is_dir()
    for body, msg in (({"ref": "41", "folder": "a/b/c"}, "Y1/Folder"), ({"ref": "41", "folder": "Stuff/x"}, "Y1/Folder"), ({"ref": "41", "folder": "Y1"}, "year folder")):
        s, r = req(web, "/api/courses/add", body)
        assert s == 400 and msg in r["error"], r


def test_moving_the_study_folder_up_a_level_keeps_every_record(web):  # noqa: F811
    """Y3 was the study folder; now its parent is, so that Y1 and Y2 can sit beside it."""
    import os
    f = next(f for f in req(web, "/api/alloc")[1]["files"] if f["name"] == "Workshop 1.pdf")
    req(web, "/api/download", {"keys": [f["key"]]})
    wait_job(web)
    web.app.refresh()                                              # records where the study folder is
    old = web.root
    assert old.name == "Y3" and (old / "CSCI.999 Test Module" / "Workshops" / "Workshop 1.pdf").exists()
    web.ctx.settings.dest_root = old.parent                        # what Settings > Folders does
    web.app.refresh()
    c = by_id(web)[7]
    assert c["tracked"] and c["folder"] == "Y3/CSCI.999 Test Module"
    files = {f["name"]: f for f in req(web, "/api/alloc")[1]["files"]}
    assert files["Workshop 1.pdf"]["status"] == "downloaded" and files["Workshop 1.pdf"]["rel"] == "Y3/CSCI.999 Test Module/Workshops/Workshop 1.pdf"
    assert files["01 Intro.pdf"]["status"] == "new" and files["01 Intro.pdf"]["folder"].startswith("Y3/CSCI.999 Test Module/")
    web.ctx.settings.dest_root = old                               # and back down again
    web.app.refresh()
    assert by_id(web)[7]["folder"] == "CSCI.999 Test Module"
    assert {f["name"]: f for f in req(web, "/api/alloc")[1]["files"]}["Workshop 1.pdf"]["rel"] == "CSCI.999 Test Module/Workshops/Workshop 1.pdf"


def test_a_module_folder_moved_into_a_year_folder_by_hand_is_followed_not_doubled(web):  # noqa: F811
    """Without this the old place stays in the tree as a folder that 'will be created', next to the real one in Y3."""
    import shutil
    f = next(f for f in req(web, "/api/alloc")[1]["files"] if f["name"] == "Workshop 1.pdf")
    req(web, "/api/download", {"keys": [f["key"]]})
    wait_job(web)
    req(web, "/api/courses/add", {"ref": "7", "folder": "CSCI.999 Test Module"})          # also when the course was added by hand
    wait_job(web)
    (web.root / "Y3").mkdir()
    shutil.move(str(web.root / "CSCI.999 Test Module"), str(web.root / "Y3" / "CSCI.999 Test Module"))
    web.app.refresh()
    assert by_id(web)[7]["folder"] == "Y3/CSCI.999 Test Module"
    files = {f["name"]: f for f in req(web, "/api/alloc")[1]["files"]}
    assert files["Workshop 1.pdf"]["status"] == "downloaded" and files["Workshop 1.pdf"]["rel"].startswith("Y3/CSCI.999 Test Module/")
    assert all(f["folder"].startswith("Y3/") for f in files.values())
    top = [k["name"] for k in req(web, "/api/tree")[1]["root"]["kids"]]
    assert top == ["Y3"] and not (web.root / "CSCI.999 Test Module").exists()               # one place only


def test_a_course_followed_into_a_new_folder_goes_back_to_its_existing_folder_in_a_year(web):  # noqa: F811
    """The study folder moved up to hold Y1..Y3, the course was followed again into a fresh top-level folder: no second copy."""
    web.mock.courses.append({"id": 51, "fullname": "LZSCC.321 Languages and Compilation", "shortname": "LZSCC.321"})
    web.mock.sections[51] = []
    web.mock.add_file(51, 1, 800, "Lecture 1.pdf", PDF + b"z")
    web.app.refresh()
    req(web, "/api/courses/add", {"ref": "51"})                                            # top-level 'LZSCC.321 Languages and Compilation'
    wait_job(web)
    assert by_id(web)[51]["folder"] == "LZSCC.321 Languages and Compilation"
    (web.root / "Y3" / "LZSCC.321 Compilers").mkdir(parents=True)                         # the folder you already had, in its year
    web.app.refresh()
    c = by_id(web)[51]
    assert c["folder"] == "Y3/LZSCC.321 Compilers" and c["added"] is False
    assert not (web.root / "LZSCC.321 Languages and Compilation").exists()
    files = [f for f in req(web, "/api/alloc")[1]["files"] if f["course"] == 51]
    assert files and all(f["folder"].startswith("Y3/LZSCC.321 Compilers") for f in files)
    assert "LZSCC.321 Languages and Compilation" not in [k["name"] for k in req(web, "/api/tree")[1]["root"]["kids"]]
    (web.root / "Keep").mkdir()
    (web.root / "Keep" / "mine.txt").write_text("x")                                      # a followed folder that holds files is never given up
    web.mock.courses.append({"id": 52, "fullname": "LZSCC.339 Quality", "shortname": "LZSCC.339"})
    web.mock.sections[52] = []
    web.app.refresh()
    req(web, "/api/courses/add", {"ref": "52", "folder": "Keep"})
    wait_job(web)
    (web.root / "Y3" / "LZSCC.339 Q").mkdir()
    web.app.refresh()
    assert by_id(web)[52]["folder"] == "Keep"


def test_folder_names_follow_your_module_folders(web):  # noqa: F811
    n = web.app._folder_name
    assert n({"fullname": "LZSCC.321: Languages and Compilation (2025/26)", "shortname": "LZSCC.321-25"}) == "LZSCC.321 Languages and Compilation"
    assert n({"fullname": "Deep Learning", "shortname": "LZSCC323"}) == "LZSCC.323 Deep Learning"          # your folders use the dot
    assert n({"fullname": "MATH101 - Calculus [2023-24]", "shortname": ""}) == "MATH.101 Calculus"
    assert n({"fullname": "Careers Hub", "shortname": "CAREERS"}) == "Careers Hub"
    (web.root / "ABCD200 Plain").mkdir(); (web.root / "ABCD201 Plain").mkdir()                              # now most have no dot
    assert n({"fullname": "Deep Learning", "shortname": "LZSCC.323"}) == "LZSCC323 Deep Learning"
