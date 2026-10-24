# What the app asks of Moodle

The app only reads. It uses the Moodle mobile web service with your personal token (`moodle-app probe` lists what your site allows and
writes `probe_report.json` with field names only, no values). Written for Moodle 4.x; sites differ in which functions are enabled.

## Functions the app uses
| Need | Function | Notes |
|---|---|---|
| Courses | `core_enrol_get_users_courses` | `startdate`, `enddate`, `hidden`, `visible` |
| Files and structure | `core_course_get_contents` | sections, modules, file URLs; Moodle 4.5 `subsection` modules are filed under the week of their parent section |
| Assignments (due, cut-off, intro files) | `mod_assign_get_assignments` | |
| Submitted / graded | `mod_assign_get_submission_status` | status, grading status, extension |
| Calendar deadlines | `core_calendar_get_action_events_by_timesort` | each event has a direct link and a course |
| Quizzes (open/close) | `mod_quiz_get_quizzes_by_courses` | |
| Announcements | `mod_forum_get_forums_by_courses` (type `news`) and `mod_forum_get_forum_discussions` | |
| Links, pages, folders | `mod_url_/page_/folder_get_*_by_courses` | |
| Change detection | `core_course_get_updates_since` | |

## May not be available on your site
Grade reports (`gradereport_user_get_grade_items`), per-course completion status and participant lists depend on site permissions. The app
does not need them: missing functions are skipped and recorded in the activity log.

## Never called
Every function that writes (submitting work, creating calendar events, sending messages, changing completion) is never called.
Submission links are built as `<site>/mod/assign/view.php?id=<cmid>`.

## Field names
Assignment fields (`duedate`, `cutoffdate`, `allowsubmissionsfromdate`, `cmid`, `nosubmissions`) are the documented Moodle 4.x ones and are read with
`.get()`: a missing field is recorded as a warning in the activity log instead of stopping a sync.
