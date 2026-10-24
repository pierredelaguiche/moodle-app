"""moodle-app: a local Moodle companion (files, deadlines, announcements, on-request notes) backed by SQLite.

Layers, outermost first (each may import only from the ones below it):
    cli / web        entry points; no business logic
    services         use-cases: sync, files, deadlines, announcements, notes, ics, tree
    notes, llm       study-material generation (manual, token-guarded) and LLM providers
    moodle           the only code that talks to Moodle (client, fetchers, capabilities)
    db               connection, migrations, repositories (the only code that writes SQL)
    domain           pure functions and dataclasses (classify, duplicates, weeks); no I/O except reading a folder
    config, host     settings/data directory; operating-system actions (trash, open in editor)
"""
__version__ = "0.1.0"
