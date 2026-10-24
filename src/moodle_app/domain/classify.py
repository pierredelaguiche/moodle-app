"""Which category folder a Moodle file belongs in. First keyword hit over (file name, activity name, section name) wins."""
from __future__ import annotations

import re

from .util import norm

FALLBACK = "Other"
NUMBERED_LECTURE = re.compile(r"^\d{1,2}\s")      # "00 intro", "01 QualityScalability" -> Lectures


def classify(categories, *texts):
    for text in texts:
        t = norm(text)
        for folder, kws in categories.items():
            for k in kws:
                if re.search(r"(?<![a-z])" + re.escape(k) + r"(?:s|es)?(?![a-z])", t):
                    return folder
    if NUMBERED_LECTURE.match(norm(texts[0] if texts else "")):
        return "Lectures"
    return FALLBACK
