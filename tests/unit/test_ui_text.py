"""UI text stays short: no long string literals in the page scripts, and card intros fit one line.

Phase 1 of docs/DESIGN_AI.md cut repeated and long text; these checks stop it creeping back. The Help page (guide.js)
is prose by design and is exempt."""
import re

from moodle_app.web import server

MAX_LITERAL = 140            # any string literal in a page script
MAX_INTRO = 110              # a card intro: about one line of a card at the default window width
PROSE = {"guide.js"}
SVG_PATH = re.compile(r"[MLHVCSQTAZmlhvcsqtaz0-9 .,-]+")
NAME = re.compile(r"[A-Za-z_$][\w$]*")
INTRO = re.compile(r"h\('p', \{ class: '(?:muted|hint|faint)[^']*'")


def literals(src):
    """[(start, end, line, text)] for every string and template literal in JavaScript source.

    Comments and regex literals are skipped; a `${...}` in a template counts as one character, since its length
    on screen is not known here."""
    out, i, n, line, prev = [], 0, len(src), 1, ""
    while i < n:
        c = src[i]
        if c == "\n":
            line, i = line + 1, i + 1
        elif c.isspace():
            i += 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line, i = line + src.count("\n", i, j), j
        elif c == "/" and (prev == "" or prev in "(,=:[!&|?{};+-*%<>~^" or prev == "kw"):       # a regex, not a division
            j, cls = i + 1, False
            while j < n and src[j] != "\n" and (src[j] != "/" or cls):
                if src[j] == "\\":
                    j += 1
                elif src[j] in "[]":
                    cls = src[j] == "["
                j += 1
            i, prev = j + 1, "x"
        elif c in "'\"`":
            j, buf, start = i + 1, [], line
            while j < n and src[j] != c:
                if src[j] == "\\":
                    buf.append(src[j + 1:j + 2])
                    j += 2
                elif c == "`" and src.startswith("${", j):
                    k, depth = j + 2, 1
                    while k < n and depth:
                        depth += {"{": 1, "}": -1}.get(src[k], 0)
                        k += 1
                    buf.append("…")
                    line, j = line + src.count("\n", j, k), k
                else:
                    line += src[j] == "\n"
                    buf.append(src[j])
                    j += 1
            out.append((i, j + 1, start, "".join(buf)))
            i, prev = j + 1, "x"
        else:
            m = NAME.match(src, i)
            if m:
                prev, i = ("kw" if m.group() in ("return", "typeof", "case", "in", "of") else "x"), m.end()
            else:
                prev, i = c, i + 1
    return out


def intro_lengths(src):
    """[(line, length)] of the text of every `h('p', { class: 'muted' | 'hint' | 'faint…', text: … })`: the literals of
    the `text:` value, added up. Text that is not a literal (data from the server) counts as nothing."""
    lits, out = literals(src), []
    for m in INTRO.finditer(src):
        t = src.find("text:", m.end())
        if t < 0 or src[m.end():t].strip(" ,") != "":             # another property first: not the shape checked here
            continue
        pos, depth, total, line = t + 5, 0, 0, src.count("\n", 0, t) + 1
        while pos < len(src):
            lit = next((x for x in lits if x[0] == pos), None)
            if lit:
                total, pos = total + len(lit[3]), lit[1]
                continue
            ch = src[pos]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                break
            pos += 1
        out.append((line, total))
    return out


def scripts():
    return sorted(f for f in server.STATIC.glob("*.js") if f.name not in PROSE)


def test_the_scanner_reads_strings_templates_comments_and_regexes():
    src = "const a = 'it\\'s'; // 'no'\n/* \"no\" */ const r = /['\"]/g, t = `x ${f({a: 'in'})} y`; h('p', { class: 'muted', text: 'ab' + n + `c${d}` }, 'kid');"
    assert [x[3] for x in literals(src)] == ["it's", "x … y", "p", "muted", "ab", "c…", "kid"]
    assert [x[2] for x in literals(src)][:2] == [1, 2]
    assert intro_lengths(src) == [(2, 4)]


def test_no_long_ui_string_outside_the_help_page():
    long = [(f.name, line, len(text), text[:60]) for f in scripts() for _, _, line, text in literals(f.read_text(encoding="utf-8"))
            if len(text) > MAX_LITERAL and not SVG_PATH.fullmatch(text)]
    assert long == [], f"UI strings over {MAX_LITERAL} characters: put the detail in a title tooltip or the Help page"


def test_card_intros_are_one_line():
    long = [(f.name, line, n) for f in scripts() for line, n in intro_lengths(f.read_text(encoding="utf-8")) if n > MAX_INTRO]
    assert long == [], f"intro paragraphs over {MAX_INTRO} characters: one line, the rest in a title tooltip or the Help page"
