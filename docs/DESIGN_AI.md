# Design: less text, a new AI tab, audio guides

Status: proposal, not started. No code is changed until this is approved.
Order: **1** cleanup → **2** a model per task → **3** AI tab and note modes → **4** Study quick menu → **5** audio guides → **6** two chat engines.
Each phase ships and is tested on its own.

## 1. Say it once (app-wide cleanup)
**Problem.** The same information appears several times on one screen. Example: the plan-usage sentence is built by `services/usage.line()`
and shown under the AI header, again above the plan (`paint`), and a third time in the *Create notes* dialog. Card intros run 2–4 sentences.
Long UI strings (over 110 characters): ai.js 7, settings.js 4, chat.js 3, editor.js 3, organise.js 3 (guide.js is the Help page, so long text is fine there).

**Rules**
- Each fact appears in one place. Usage appears as a single chip in the AI header (`Claude · 5h 12% · 7d 40%`); its tooltip adds the time
  it was checked, extra usage, and the tokens sent in the last 7 days. Dialogs mention usage only when it matters: `limit_problem`, or a window above 60%.
- A card intro is at most one line. Anything longer goes into a `title` tooltip or the Help page.
- Status words beat sentences ("Not connected" + *Connect*, not a paragraph).

**Changes**
- `services/usage.py`: `line()` → `summary()` returning data `{five_hour, seven_day, overage, seen, app_tokens_7d}`. `limits_line` stays for the CLI.
- `services/ai.py`: `state/check/plan/notes_job` return `usage` as that dict. `ai.js` formats it in one helper (`usageChip`).
- Shorten the long strings listed above. Add a unit test that fails on UI string literals over 140 characters outside `guide.js` (stops it creeping back).

## 2. A model per task
Each AI task can use its own model: for example Claude for notes and a local model for the audio script.
The top-level `provider`/`model` stay the default for every task; `tasks` overrides them per task (`config.json`):
```json
"llm": {"provider": "auto", "model": "haiku",
        "tasks": {"audio_script": {"provider": "openai_compat", "base_url": "http://localhost:11434/v1", "model": "qwen3:14b"},
                  "chat": {"provider": "cli", "command": ["opencode", "run"], "model": "anthropic/claude-sonnet"}}}
```
- Tasks: `notes`, `guide`, `audio_script`, `chat` (Study assistant and *Chat with the course*), `ask`. A task without an entry uses the default.
- Providers: today's `claude` (API key), `claude_cli` and `openai_compat` (covers Ollama, LM Studio, llama.cpp server, vLLM), plus a new generic
  **`cli`** provider: it runs a command with the prompt on stdin (or as an argument) and reads the reply from stdout. That covers harnesses
  such as `opencode run`. Plan-limit tracking stays specific to Claude. Other providers report tokens when they can, otherwise an estimate.
- *Settings › Connections › AI model* becomes a small table (task → provider, model, *Test*). Each plan and confirm names the model it will use.
- Privacy: a local provider keeps the text on the machine. The confirm says "stays on this computer" or "sent to <provider>".

**Changes**: `llm/providers.py` (`CLIProvider`, `make_provider(cfg, task=)`), `services/ai.py` (`provider(task)` everywhere),
`config.py` (`llm.tasks.*` fields), `settings.js` (the table). Tests: task fallback, the CLI provider against a fake command (like `fake_claude.py`).

## 3. AI tab: one "Create" panel
```
AI   [Claude · 5h 12% · 7d 40%]                         [Library] [Prompts]
┌ Create ───────────────────────────────────────────────────────────────┐
│ Module [CSCI.323 ▾]   Make [Notes ▾ | Study guide | Audio guide]      │
│ Notes:  Group [Per week | Per category (whole term)]   Week [all]     │
│         Kinds ☑ Lectures ☑ Workshops ☐ Code ☐ Coursework              │
│ [Show plan]  → editable plan (as today) → [Create 4 notes]            │
└───────────────────────────────────────────────────────────────────────┘
┌ Chat with the course ┐  (replaces "Ask": same backend as the Study assistant)
┌ Library ┐  notes, study guides, audio (with a player), deadlines from notes
```
- **Per week** (today): `kind|week:N` → `Notes/Lectures/Week 03.md`.
- **Per category** (new): one note per kind for the whole term, `kind|term` → `Notes/Lectures/Lectures - all weeks.md`.
  Source: the weekly notes when they exist and are current (cheap, same pattern as the study guide), otherwise the raw files
  in batches with a merge step. The plan shows which source it will use ("from 9 weekly notes" / "from 34 files: 3 parts + merge").
- **Study guide**: unchanged kind, now a first-class choice under *Make*.
- The front matter keeps `sources:`, so the Tracker's *Notes ✓* also works for term notes.

**Changes**
- `notes/planner.py`: `build_plan(..., group="week"|"term")`, new job key `term_key(kind)`, output path, and batching reused from `guide_batches`.
- `notes/prompts.py`: `category_notes` and `category_notes_merge` (version 1). Existing prompts stay as they are.
- `services/ai.py`: `plan/notes_job` accept `group`. `config.py`: `notes.group` default, plus `max_output` for the new kinds.
- `web/static/ai.js`: rebuilt around the panel above. "Ask" is merged into "Chat with the course".

## 4. Study assistant: quick menu + chat
The top of the assistant panel gets buttons computed from what is open:
`Notes for week 3` · `Lectures, all weeks` · `Study guide` · `Audio guide for this note` · `Open in AI tab ›`.
Each one asks for the plan of one job, shows the usual confirm (destination, token estimate), then runs it as a job. Nothing is sent without
that confirm. The chat below stays. New: *+ Module notes* pins all of the module's notes as context (capped at `notes.max_input_tokens`).

**Changes**
- `editor.js`: a `quickMenu()` above the chat. The week comes from the focused file (`/api/ai/week?rel=`, reusing `planner.week_of`).
- `web/server.py`: the Study page already has `X-Page: study` for every LLM path. Extend the unit test that lists which script may name
  which path (study: `plan`, `notes`, `audio/*`, `chat`).
- `ai.js` reads `#/ai/<prefill>` so *Open in AI tab* lands with the form filled in.

## 5. Audio guides
Two steps, both visible:
1. **Spoken script** (LLM, task `audio_script`, so it can run on a local model). The source (a note, a study guide or any text file) is
   rewritten for listening, as a read-along that explains *more* than the page: numbers, symbols and formulas spelled out
   ("x squared plus two x", "ten to the minus three"), each step of a derivation said in words, a short explanation where the page only lists,
   abbreviations expanded, tables and code described, not read. The section headings of the source are kept as spoken signposts so you can
   follow it on screen. A recap closes it. Length is set in minutes (about 1,000 characters ≈ 1 minute). Saved as `Notes/Audio/Week 03 - script.md`
   (editable, protected by the manifest like notes), then spoken. A long source is rewritten section by section, with no merge step.
   The `audio_script` prompt has an explicit maths rule: every formula, symbol, unit, subscript and Greek letter is written as it is spoken,
   then explained in one plain sentence. The rest of the pipeline therefore never sees raw maths.
2. **Speech** (TTS, local). The script is split by paragraph (≤ 1,500 characters), each piece is spoken, and the pieces are joined into
   `Notes/Audio/Week 03.mp3` (or `.wav`). It is a background job like downloads: the page stays usable, and it can be cancelled between pieces.
   Resuming skips pieces already done.

**Time estimate and progress**
- Audio length ≈ script characters ÷ 1,000 minutes (÷ speed). Generation time = audio length × the engine's measured speed factor.
- The speed factor is measured, not guessed: *Test voice* in Settings speaks one sentence and stores the factor per engine. Every finished job
  updates it (running average). Before the first measurement, the estimate shows "about N min (estimate improves after the first piece)".
- The estimate appears next to *Make audio* and in the confirm ("~11 min of audio, ready in about 4 min on this computer"). Script generation
  shows the usual model and token line.
- Progress bar: pieces done / total and seconds of audio made, with the time left recalculated after each piece. It sits in the job list and on
  the button that started it. This app-side bar works for both engines; neither engine's own progress output is relied on.

**Voice settings** (Settings › Voice, and on the AI tab next to *Make audio*, saved per user):
- **Speed**: 0.5–2.0 in steps of 0.05, default 1.0, with a short *Preview* (one sentence, nothing saved).
- **Language and accent**: the voice list comes from the engine (`GET <base_url>/audio/voices` for the container, the voices bundled with
  the in-process engine), grouped by language and accent (American, British, Spanish, French, Hindi, Italian, Japanese, Portuguese, Chinese).
- **Default**: English, `af_heart` (American; the only grade-A voice in Kokoro's own voice ratings). For British English, `bf_emma` (B-).
  The script prompt follows the chosen language, so a French voice gets a French script.

**Engines (both, chosen in order):**
| Engine | How | Notes |
|---|---|---|
| Container (default) | POST `<base_url>/audio/speech` (OpenAI-style), e.g. Kokoro-FastAPI on port 8880, `model: kokoro`, `voice: bf_emma` | no new Python dependency; any compatible server works; returns mp3 |
| In-process (fallback) | optional extra `pip install ".[audio]"` → `kokoro`, `soundfile` (pulls in PyTorch; needs `espeak-ng`) | 24 kHz WAV, joined with the stdlib `wave` module; mp3 only if `ffmpeg` exists |

`tts.provider: auto` tries the container URL, then the in-process engine, then reports "Voice: not set up". Script text never leaves the
machine at this step. Size: WAV ≈ 2.9 MB per minute, mp3 about a tenth of that, so the container's mp3 is the default.

**Changes**
- New `tts/providers.py`: the only code that knows TTS APIs (mirrors `llm/providers.py`), with `diagnose()` for Settings.
- New `services/audio.py`: `script_job`, `speak_job` (chunk, synthesise, join, resume). It uses `notes_manifest`, so edited scripts are never overwritten.
- `services/ai.py` + `web/server.py`: `/api/ai/audio/script`, `/api/ai/audio/speak`, `/api/ai/tts/check`.
- `config.py`: `tts.provider|base_url|model|voice|format|lang`. `App.connections`: a 5th entry, **Voice**.
- Playback: `files.js` treats `mp3|wav|m4a` as previewable (an `<audio>` element), `/api/raw` serves audio types, and the CSP gains `media-src 'self' blob:`.
- Docker (roadmap B): `compose.yaml` gets a `kokoro` service (`ghcr.io/remsky/kokoro-fastapi-cpu`) and `TTS_BASE_URL=http://kokoro:8880/v1`.

## 6. Chat: two engines, Claude Code in a container
The Chat page today has one engine: it runs the `claude` program (Claude Code) inside a folder you pick. It gets a second engine and a setting:

| Engine | What it is | Providers |
|---|---|---|
| **Claude Code** (agent) | today's chat: Claude reads (and, if allowed) edits files in a project folder | your Claude sign-in, or an Anthropic API key |
| **Model chat** | a plain chat; you add files as context, and nothing runs on your computer | any provider from section 2 (API keys, Ollama, LM Studio, opencode, …) |

- *Settings › Chat engine*: Claude Code / Model chat (default: Claude Code if it is found, otherwise Model chat).
- When `claude` or `claude_cli` is chosen as the chat provider, the page offers Claude Code ("Use Claude Code? It can work inside your
  project folders"). It is a suggestion; the setting decides.

**Claude Code inside the Docker setup.** Anthropic publishes an official way to put Claude Code in a container: the Dev Container Feature
`ghcr.io/anthropics/devcontainer-features/claude-code` (or `npm install -g @anthropic-ai/claude-code` in the Dockerfile). The app image installs it.
- **Sign-in**: there is no browser inside the container. You run `claude setup-token` once on your computer and paste the token in *Settings ›
  Connections › Claude*. It is stored like the Moodle token (mode 0600) and passed to Claude as `CLAUDE_CODE_OAUTH_TOKEN`. An Anthropic API
  key also works. Some users report 401 errors with setup tokens in containers, so *Test* runs one tiny `claude -p` call and shows the real error.
- **Project folders**: Claude in the container sees only folders mounted into it. `compose.yaml` mounts the study folder and a `projects`
  folder you choose. *+ Add folder* lists only what is mounted and explains how to mount more. Claude.ai "Projects" from your account are not
  reachable from Claude Code; only folders on disk are.
- Outside Docker nothing changes: the app uses the `claude` already signed in on your computer.

**Changes**: `services/chat.py` (an engine switch; the Model-chat engine reuses `AI.chat` with task `chat`), `chat.js` (engine label, file
context for Model chat), `config.py` (`chat.engine`), `setup.py` (store the Claude token), `Dockerfile`/`compose.yaml` (roadmap B).

## Tests (no network, as today)
- Planner: term grouping, its keys and source choice; the batches + merge path.
- Audio: a mock TTS server (like the mock model servers) for chunking, joining, cancel/resume and manifest protection; `auto` engine order.
- Web: the X-Page rules for the new paths; e2e checks that usage text appears once on the AI tab and that the quick menu shows a confirm before sending.
- Audio estimate: the factor update and the estimate text with a fake engine that has a fixed speed.
- Chat: engine choice and fallback; Model chat against the mock model server; the token is passed as an env var and never logged.

## Open points
- A small local model may follow the maths rule less well than Claude. Compare one week of real notes before choosing a default for `audio_script`.
