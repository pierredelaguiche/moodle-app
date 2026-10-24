"""Prompts for `create-notes`. Each has a version (bump it when the text changes: it invalidates 'up to date' checks).

Override any of them without touching code: put a file named <prompt name>.md in the folder given by config
`notes.prompts_dir`. The file replaces the instructions; the placeholders {{module}} {{title}} {{week}} {{source}}
{{part}} {{text}} are filled in. An override is versioned by its content hash.
"""
import hashlib
import re
from pathlib import Path

SYSTEM = (
    "You write study material for a university student from the course text you are given. "
    "Use only the supplied text; do not add facts that are not in it. If something is unclear, cut off or looks like an "
    "image-only slide, say so briefly instead of guessing. Cite slide/page numbers as (p.N) using the <!-- p.N --> markers. "
    "Be concise and exact: definitions, formulas, algorithms and worked examples matter more than prose. "
    "Write maths as LaTeX between dollar signs: $x^2$ inline, $$ ... $$ on its own lines for display. Never put maths in backticks."
)

DEFAULTS = {
    "lecture_notes": (1, """Make lecture notes from the lecture text below.

Module: {{module}}
Lecture: {{title}}  (week {{week}}) {{part}}

Format (Markdown, no preamble):
# <lecture title>
## Overview  (3-5 lines: what this lecture is for and what it builds on)
## Key concepts  (each as **term**: definition, with (p.N))
## Details  (the argument in order: methods, algorithms, formulas in LaTeX $...$, worked examples)
## Likely exam points  (bullet list of what a student must be able to do or state)
## Gaps / unclear  (only if the text was incomplete or image-only)

Lecture text:
{{text}}"""),

    "workshop_summary": (1, """Summarise the workshop sheet below for a student who is about to attempt it.

Module: {{module}}
Workshop: {{title}}  (week {{week}}) {{part}}

Format (Markdown, no preamble):
# <workshop title>
## Goal  (2-3 lines)
## Questions  (numbered, one line each: what is asked, and the concept from the course it exercises)
## What you need first  (definitions or methods to revise; cite (p.N))
## Approach hints  (how to start each question; hints only, do NOT give full solutions)

Workshop text:
{{text}}"""),

    "workshop_code": (1, """Turn the workshop sheet below into a Python file the student fills in.

Module: {{module}}
Workshop: {{title}}  (week {{week}}) {{part}}

Rules:
- Output ONLY the contents of one .py file, no Markdown fences, no explanation.
- Start with a module docstring: module, workshop title, how to run it.
- For each question: a comment block with the question text copied faithfully (including its number and marks if given),
  then a stub to fill in: a function with a docstring and `# TODO` and `raise NotImplementedError` (or `pass`), or for
  written answers a variable such as `answer_3 = ""  # TODO`.
- Include any data or setup code the sheet provides. Do not solve the questions.
- If the sheet has no programming, still keep one stub per question so answers can be typed under the comments.
- Finish with `if __name__ == "__main__":` that calls each stub inside try/except NotImplementedError and prints what is still to do.

Workshop text:
{{text}}"""),

    "coursework_notes": (1, """Make notes on the coursework document below, for the student who has to do it.

Module: {{module}}
Document: {{title}} {{part}}

Format (Markdown, no preamble):
# <coursework title>
## What is asked  (the task in a few lines, in your own words)
## Deliverables  (what to hand in, in which format, where)
## Deadlines  (every date and time the text gives, with what happens then)
## Marking  (weights, criteria, penalties, if stated)
## Requirements checklist  (checkbox list: "- [ ] ...", one per concrete requirement)
## Unclear or missing  (anything the document leaves open)

Finish with exactly one last line, a machine-readable list of the dates above (an empty list if there are none):
<!-- deadlines: [{"title": "<what is due>", "due": "YYYY-MM-DD", "time": "HH:MM"}] -->
Use "time": "" when no time is given. Only dates the text states; never guess a year the text does not give.

Document text:
{{text}}"""),

    "study_guide": (1, """Condense the notes below into one study guide for the module.

Module: {{module}} {{part}}

Format (Markdown, no preamble):
# {{module}}: study guide
## How the course fits together  (a short map: topics in order and how they depend on each other)
## Week by week  (per week: 3-6 bullets with the ideas that must be known)
## Definitions and formulas  (one consolidated list, grouped by topic)
## Workshop skills  (what the workshops practised, per week)
## Common mistakes and exam focus  (only what the notes support)
## Revision checklist  (checkbox list: "- [ ] I can ...")

Keep the week labels from the notes. Notes:
{{text}}"""),

    "study_guide_merge": (1, """Below are partial study guides, each written from a different part of the same module. Merge them into ONE study guide
with the same structure (map, week by week, definitions and formulas, workshop skills, mistakes and exam focus, revision
checklist). Remove duplicates, keep the week order, lose nothing that is only in one part.

Module: {{module}}

Partial guides:
{{text}}"""),
}


def get(name, prompts_dir=None):
    """Return (version, template) for a prompt, honouring a user override file."""
    version, tpl = DEFAULTS[name]
    if prompts_dir:
        f = Path(prompts_dir).expanduser() / f"{name}.md"
        if f.is_file():
            tpl = f.read_text(encoding="utf-8")
            return f"custom-{hashlib.sha256(tpl.encode()).hexdigest()[:8]}", tpl
    return f"{name}_v{version}", tpl


def render(tpl, **values):
    """Single pass, so placeholder-looking text inside course material is never substituted."""
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m.group(1), m.group(0))), tpl)
