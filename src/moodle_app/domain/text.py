"""Small text helpers shared by services and the web layer."""
from __future__ import annotations

from html.parser import HTMLParser


def html_to_text(html):
    """Announcement bodies come from teachers as HTML. Everything downstream only ever receives plain text."""
    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.out = []
            self.skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self.skip += 1
            elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
                self.out.append("\n")

        def handle_endtag(self, tag):
            if tag in ("script", "style") and self.skip:
                self.skip -= 1
            elif tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
                self.out.append("\n")

        def handle_data(self, d):
            if not self.skip:
                self.out.append(d)
    p = P()
    p.feed(html or "")
    text = "".join(p.out)
    lines = [line.rstrip() for line in text.splitlines()]
    out = []
    for ln in lines:                      # at most one blank line in a row
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()
