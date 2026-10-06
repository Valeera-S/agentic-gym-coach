# Agentic Gym Coach

A general-purpose, local-first gym coach you talk to like a real one. It
runs one standardized intake — scanning what it already knows and asking
only for what's missing — remembers your profile, logs your sessions, gates
every exercise suggestion against your injuries, and answers training +
nutrition questions from vendored professional sources — never invented
science.

**First conversation:** the coach introduces what it can do, then runs a
short intake in 3 rounds (goal and starting point, your week, calibration and
safety) with visible progress. Where a choice changes what the coach does
(e.g. a "ripped" physique target switches on cut rules), the options spell
out the consequence before you pick. It then coaches toward YOUR profile. Same flow when you return after months away: what's stored gets
cited and re-confirmed, not re-collecting from scratch.

```text
You:  I want to put size on my side delts. 4 days a week, commercial gym.
Coach: <intake scan; asks only what's missing>
       → builds a program from the training pyramid,
       volume matched to your training age, 2×/week delts.

You:  Log today: Incline Bench 3x8 @ RPE 8, Lateral Raise 4x12 @ RPE 9
You:  Should I do skull crushers tonight?        → deterministic safety gate
You:  How many calories to get ripped?           → nutrition pyramid ch02
You:  My bench is stuck.                         → plateau flowchart, free wins first
You:  Remember I hate barbell rows.              → long-term memory (manual only)
```

## What the coach can do

Just talk to it; it picks the tool. Each tool is named once, with something
you could say.

**Logging**
- `coach_log_session`: "Log today: Incline Bench 3x8 @ RPE 8, Lateral Raise 4x12 @ RPE 9." Pounds or kilos.
- `coach_sessions` and `coach_session_detail`: "Show my last 5 sessions", "show me Tuesday's session exactly as I entered it."

**Analysis**
- `coach_trend`: "How are my side delts progressing?" Hard sets, avg RPE, est 1RM, trend direction, stall flag.
- `coach_snapshot`: "Where am I in my training block?" A 4-week anchor with time since the last deload and how long you were away.
- `coach_recovery`: "Am I recovered enough to push today?" A 0-100 heuristic score.
- `coach_doctrine`: "What does the book say about plateaus?" The two vendored books, on demand.

**Corrections**
- `coach_session_amend`: "Fix Tuesday: bench was 3x6, not 3x8."
- `coach_session_delete`: "Delete the duplicate session on the 4th." Both are audited and restorable.

**Safety**
- `coach_safety_check`: "Should I do skull crushers tonight?" Deterministic check against your injuries; it offers alternatives.
- `coach_injuries_list` and `coach_injuries_seed`: "What injuries do you have on file?", "my left elbow hurts on extensions."

**Bodyweight**
- `coach_bodyweight_log`: "Weighed 81.2 kg this morning, fasted."
- `coach_bodyweight_history`: "Show my weight trend."
- `coach_bodyweight_amend` and `coach_bodyweight_delete`: "That reading was 80.2, not 82.0", "delete yesterday's reading."

**Memory and profile**
- `coach_intake_status`: "What do you still need to know about me?" The intake in 3 short rounds, with progress shown.
- `coach_profile_get` and `coach_profile_set`: "What do you have on file for me?", "I can train 4 days a week now."
- `coach_memory_save` and `coach_memory_search`: "Remember I hate barbell rows" (saved only when you say so), "what did I say about rows?"

## How it works

- **Doctrine from books, not vibes.** Two vendored skills — *Muscle & Strength
  Pyramid: Training* (2nd ed.) and *Muscle & Strength Nutrition Pyramid* — are
  the only professional knowledge, loaded on demand (procedural disclosure).
- **You are data.** Goals, training age, schedule, priorities, injuries, and
  memory notes live in DuckDB as validated, versioned records — the coach
  never hardcodes who you are. Goal changes are audited.
- **It verifies.** `coach_safety_check` cross-references `injury_status`
  before any exercise suggestion. Deterministic — no negotiation.
- **It measures honestly.** Volume = effective hard sets (form-discounted,
  overlap-inclusive, with every credited muscle derived from the book's
  counting chart). 1RM estimates only from ~5RM-or-heavier sets. Progress is
  judged per exercise (more load or more reps), not by set counts. Missing
  data is stated, never fabricated; an unknown exercise is flagged, never
  dumped into a real muscle.
- **It keeps your numbers.** Log in pounds or kilos (`weight` + `unit`);
  read any session back exactly as entered (`coach_session_detail`), and
  correct or delete a wrong entry (`coach_session_amend` /
  `coach_session_delete`, both audited and restorable).
- **It works everywhere.** One Python core; every MCP-capable runtime
  (Claude Code, ZCode, Cursor, Codex CLI, …) attaches the same
  `mcp_server.py`.

## Stack

Python 3.11+ · DuckDB · Polars · Pydantic V2 · Alembic · MCP

## One-time setup

```bash
uv venv .venv && uv pip install -r requirements.txt
alembic upgrade head      # bootstrap data/gym_coach.duckdb
python scripts/ingest_log.py --reset   # optional: import historical log.md
```

## Run the Coach

- **Any MCP runtime:** point it at `.venv/bin/python mcp_server.py` and use
  [docs/COACH_PROMPT.md](docs/COACH_PROMPT.md) as the agent's system prompt —
  see [docs/adapters.md](docs/adapters.md) for per-runtime wiring.
- **Windows:** the committed `.mcp.json` uses the POSIX venv path
  (`.venv/bin/python`), which does not exist on Windows. Register a
  local-scope server instead — it overrides the project file for you only:

  ```powershell
  claude mcp add gym-coach --scope local -- .venv\Scripts\python.exe mcp_server.py
  ```

  and use `.venv\Scripts\python.exe` wherever the docs say `.venv/bin/python`.
- **Migrations:** after updating, run `alembic upgrade head` (it is safe while
  the MCP server sits idle — the server releases the DB between calls), then
  restart the runtime so the MCP server loads the new code.

## Running tests

```bash
python -m pytest -q          # 1295 tests
python -m pytest -m slow -q  # 3 perf guards on 10K-row synthetic sets
```

## Design principles

- Skills are pure deterministic code — zero LLM logic inside them.
- The safety gate is the only source of truth for contraindications.
- Volume is hard sets (form_quality < 3 discounts 50%), never tonnage.
- Long-term memory writes never happen without an explicit user command.
- The DuckDB file is canonical; full offline operation.
