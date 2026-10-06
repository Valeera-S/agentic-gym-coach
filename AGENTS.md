# AGENTS.md — Agentic Gym Coach (v2)

Workspace state: **v2.4 — data-correctness pass (2026-10-05).** Exercise names are exercise IDENTITIES (a different implement = a different identity; `models/exercise_catalog.py`), and every credited muscle is DERIVED from the Training ch03 counting chart (`models/doctrine_ch03.py`: pattern rows + documented conditionals, KNOWN_DEVIATIONS empty, ratchet-tested). Muscle vocabulary aligned with the chart (`chest`, `front_delt`, `erectors`; `unclassified` sentinel for unmapped names, credited to no muscle); every logged exercise records `raw_name`, `muscle_source`, `load_type` and the weight + unit as entered (lb or kg; migration 0003). New tools `coach_session_detail` / `coach_session_amend` / `coach_session_delete` (amend/delete audited with full row snapshots, migration 0004); habit sessions (`kind`); trend direction judged on per-exercise performance (double progression) instead of set counts; the DB file is released after every tool call. Earlier: **v2.3 — standardized intake (2026-09-19).** Onboarding is replaced by a code-driven intake assessment: the bucket list is data (`models/intake.py::INTAKE_CHECKLIST`, every field book-cited or labeled HEURISTIC), `skills/intake.py::assess_intake()` scans stored state for collected vs missing, surfaced as `coach_intake_status` (CLI + MCP). Gates are soft per-domain (`training_ready`/`nutrition_ready`): no volunteered plans when not ready, provisional plans that name their gaps only on explicit user insistence. One flow, no modes — see `docs/adr/0001-standardized-intake-soft-gates.md` and `CONTEXT.md` (glossary). Ticket-02's staleness signal shipped inside it: `session_gap` on the snapshot + `weeks_since_last_session` on Tier-1, threshold `REASSESSMENT_GAP_WEEKS` in `skills/snapshot.py` (a labeled heuristic, not book-sourced). Earlier: v2.2 night-run maintenance pass (2026-09-17) — CLI + MCP only; dual Helms skills vendored as the only doctrine; injuries write path validates + canonicalizes via `skills/injuries.py` (single owner of the injury_status schema), one phase resolver (`skills/phase.py`), one Epley definition (`skills/metrics.py`), enforced Tier-1 3K-token cap, three-code error contract (`invalid_input` / `db` / `internal`), surface defaults single-sourced with a CI drift test (tests/test_surface_drift.py). CLI cold start ~0.16s (lancedb import is lazy). 673 tests + 3 slow perf guards passing (v2.4).

## Read order & precedence
Read before writing any code or answering architecture questions:
1. **SPEC.md** — v2 schema, skill inventory, surfaces, invariants
2. **CONTEXT.md** — the glossary; use these terms exactly (intake assessment, gate, provisional plan, …)
3. **MEMORY_PROTOCOL.md** — three-tier memory, state transitions, safety guards
4. **`docs/COACH_PROMPT.md`** — the canonical Coach persona (rendered into runtime agent files)
5. **`docs/adapters.md`** — how runtimes attach (MCP is the single tool surface)
6. **`docs/IDEAS.md`** — future feature ideas / dev notebook; consult before proposing features, record new ones there

Conflict resolution: **safety layer > vendored skills (`docs/knowledge/helms-*`) > mechanics docs.** v1 knowledge documents were removed as unsourced — never resurrect their doctrine; extend knowledge by vendoring a book-skill, not by hand-writing physiology.

## Tech stack (pinned in requirements.txt)
duckdb · polars · lancedb · pydantic · alembic · duckdb-engine · pytest · plotly · pytz (DuckDB TIMESTAMPTZ conversion) · mcp (stdio server) · Python 3.11+

- OLAP: `data/gym_coach.duckdb` (persistent file mode)
- Vectors: `data/lance_db/` (deferred — Tier 3 uses DuckDB tables in v2)
- Migrations: `migrations/` (Alembic — never hand-edit schema)

## Source of truth
**The DuckDB file is canonical.** New sessions enter via the Coach (`coach_log_session`) or `python scripts/ingest_log.py <file>` for one-shot bulk imports. The user writes only raw logs ("Squat 3x5 @ RPE 8"); everything else is derived.

## Commands
Interpreter: use `.venv/bin/python` (repo venv) — bare `python` is not on PATH and the system Python has none of the deps installed.

```bash
.venv/bin/python -m pytest -q            # full suite (673 tests, ~65s); pytest.ini addopts already skip -m slow
.venv/bin/python -m pytest -m slow -q    # 3 perf guards on 10K-row synthetic sets (run separately)
.venv/bin/python scripts/ingest_log.py --dry-run [file]   # parse a log.md, no DB writes
.venv/bin/python scripts/ingest_log.py --reset [file]     # ⚠ DELETE FROM sessions first, then re-ingest
.venv/bin/python coach_tools.py          # dispatcher — prints available subcommands (18)
alembic upgrade head                    # apply migrations (bootstrap a fresh DB this way; honours GYM_COACH_DUCKDB, creates data/)
.venv/bin/python scripts/sync_adapters.py    # render COACH_PROMPT.md → native agent files, if any registered (--check for drift)
.venv/bin/python mcp_server.py          # MCP stdio server (18 tools + coach_doctrine; incl. coach_bodyweight_log / coach_bodyweight_history over migration 0005's `bodyweight_log` — every reading carries a required measurement condition and averages are per condition; the snapshot's body_weight_kg is the 7-day morning_fasted mean)
```

No lint/typecheck config exists in this repo — don't invoke tools that aren't set up.

## Modularity & maintainability
- **Skills are standalone, deterministic Python modules** in `skills/`. Each returns Pydantic models (not dicts) — documented exceptions: `skills.sessions.list_sessions` (the lean listing, plain rows for speed) and the existing `hard_sets_by_muscle` / `tendon_summary` maps. No LLM logic lives here — reasoning happens in the orchestrator/agent.
- Adding/changing a skill → add a module + `tests/test_skills/test_<name>.py`.
- **Adding an exercise** → an `Exercise` entry in `models/exercise_catalog.py` (identity, true-synonym aliases, load_type default; isolation exercises name their `target`) + its chart row in `models/doctrine_ch03.py::EXERCISE_PATTERN`. Never hand-list muscles: they are derived from the ch03 row (`chart_credit`); a chart conditional goes in `DOCUMENTED_EXCEPTIONS` with its reason, a non-book call in `HEURISTIC_CLASSIFICATIONS`. A different implement or machine is a new identity, not an alias. Names match through `lookup_key` (lower case, `-`/`_` read as spaces, whitespace collapsed — the one normalization shared with the ban matcher), so never list a hyphen/space respelling as an alias (the build rejects the duplicate). `tests/test_skills/test_catalog_doctrine.py` fails on any drift. If the new identity is a specific implement of an existing generic identity ("Bench Press", "Fly", "Row", ...), add it to that generic's `variants` so a ban on the generic name keeps blocking it (safety gate, fail-closed); a new generic name sets `generic=True`, no load_type, and lists its `variants`. Never edit `LEGACY_ALIASES` (frozen history).
- **Name matching has ONE owner: `models/exercise_catalog.py::lookup_key`** (NFKC, casefold, invisible format chars dropped, zero-width space = word break, every non-alphanumeric char = space) — used by the catalog and the ban side; never inline a variant. `tests/test_skills/test_catalog_identities.py` fails if two identities collide under it.
- `models/` holds all Pydantic schemas. `models/enums.py` is the controlled vocabulary for DB-stored vocabularies (profile-local enums live beside their models in `models/profile.py`) — DB columns store VARCHAR (including the per-exercise `muscle_group` inside the `sessions.exercises` struct, since migration 0003), Pydantic enforces (extending a vocabulary needs no migration).
- **Adding a coach tool touches three places:** a handler in `coach_tools.py` (DISPATCH), an MCP wrapper in `mcp_server.py`, and a line in `docs/COACH_PROMPT.md`.
- Schema changes go through Alembic migrations only — never hand-edit `data/gym_coach.duckdb`.

## Dev gotchas
- **Tests never touch production data.** `conftest.py` points `GYM_COACH_DUCKDB`/`GYM_COACH_LANCE` at throwaway files in `/tmp` and wipes tables around every test. Those env vars redirect the DB anywhere — but paths are read at `skills.init` import time, so set them before the first import.
- **Zero-warning suite.** `pytest.ini` turns any DeprecationWarning raised from `skills/` into an error: pin the current behavior explicitly at the call (e.g. `explode(..., empty_as_null=True)`), never filter the warning away.
- **Parallel pytest runs are safe** — every run (e.g. two worktrees at once) gets its own `mkdtemp` DB + lance dir, removed at session end. Keep each run single-process (the suite is not tuned for xdist).
- **DB connection lifecycle.** DuckDB allows one read-write process per file. Every coach tool runs inside `skills.init.connection_scope()`: the file is opened on first use and released when the call returns, so an idle MCP server never locks out alembic, `scripts/ingest_log.py` or the CLI. A genuine simultaneous-write collision surfaces as the `db` error ("... in use by another process ...") — no silent retries. Code outside any scope (tests, one-shot scripts) keeps its connection until `close_all()`.
- **Migrations on the live DB.** After pulling a schema change run `alembic upgrade head` (safe while the MCP server sits idle). 0003 rebuilds `sessions` once (muscle vocabulary → VARCHAR, `upper_chest`→`chest`, provenance/load fields, `kind`); 0004 adds `decision_log.payload`. Both downgrades REFUSE, before changing anything, when data can't be represented — they never drop data silently. Never edit a committed migration.
- **Coach tool layering:** `mcp_server.py` and the shell both wrap the same handlers in `coach_tools.py` (JSON to stdout; `{"error": ...}` + exit 1 on failure) → skill in `skills/`. Change handlers, never the wrappers. Every handler is decorated `@_accepts(...)` (the one shared arg-key check): an unknown argument key is `invalid_input`; a new tool input must be added to that list. The MCP framework itself silently drops arguments absent from a wrapper's typed signature before the handler runs (verified in-process, P50), so the check bites on the CLI and on `coach_profile_set`'s free-form dict.
- **Edit the persona in `docs/COACH_PROMPT.md` only.** Runtimes read it directly; if a native agent-file adapter is registered in `scripts/sync_adapters.py`, re-run it after editing.
- **The Coach agent must not edit code** — `docs/COACH_PROMPT.md` restricts it to `coach_*` tools + read/grep/glob. Code changes are the coding agent's job.
- **pytz is required at runtime** for DuckDB TIMESTAMPTZ → Python conversion (memory/profile RETURNING paths).

## Repo-specific constraints (differ from defaults)
- **Polars, never Pandas** — strict.
- **Pydantic V2** validates all inputs/outputs at DuckDB boundaries; per-set arrays (reps/rpe/weight_kg) must be equal length (enforced by validator).
- **`coach_snapshot` computes AND upserts** today's `phase_snapshots` row on every call (decision 2026-09-17, resolving audit Q3/T10: idempotent upsert by snapshot_date; MEMORY_PROTOCOL §2.3 states the same policy).
- **Volume currency = effective hard sets** (form_quality < 3 ⇒ 50% discount; primary + secondary 1:1 via `SECONDARY_OVERLAP`, derived from the ch03 chart; each set credits each muscle at most once; `unclassified` credits nothing; bodyweight sets count). Tonnage is detail-only (per_hand/per_side ×2). est_1RM from reps ≤ 6 sets only.
- **Trend direction** = est-1RM, judged per exercise identity (chart primaries only; an identity votes only with heavy sets in both window halves; halves = the first floor(n/2) training sessions vs the rest), when any identity votes, else per-exercise performance (double progression; chart primaries decide — for a name the catalog doesn't know, its stored muscle; an unrecorded weight is unknown, never 0). The load-step guard (every heavier set ≥ 6 reps, within-identity Epley not lower; none for bodyweight exercises) is a labelled HEURISTIC from a human decision.
- **`safety_gate.check_exercise_safety()` is deterministic** — if it returns unsafe, never suggest that exercise; offer `SafetyResult` alternatives instead.
- **No LLM reasoning inside `skills/`** — skills are pure code.
- **Tier 3 memory writes require explicit user command** ("save this"). Never auto-write. Profiles may be written after user confirmation (echo + confirm).
- **Cite retrieved values explicitly** — never paraphrase from memory. If retrieval returns null, say "I don't have that data." Do not guess.
- **Log every plan modification** (incl. goal changes — automatic in `skills/profile.py`) to the decision audit trail. Session amend/delete write the complete pre-change row to `decision_log.payload` in the same transaction (`skills/sessions.py::restore_snapshot` puts it back and writes its own `session_restore` audit entry holding the state it overwrote — a coding-agent recovery helper, not a coach tool).
- **Doctrine questions route through the vendored skills** (`docs/knowledge/helms-*/SKILL.md`) — at most one knowledge file per turn.

## Error posture
- Halt on failure. Report exact failure point + affected data + recovery options. Log to audit trail as `system_error`.
- Never silently retry, interpolate, or fabricate physiological data. Missing = "I don't have that data."

## Validation checklist (before marking any feature complete)
- [ ] DuckDB queries <100ms on 10K-row synthetic set
- [ ] `check_exercise_safety()` blocks contraindicated exercises deterministically
- [ ] Pydantic rejects malformed session logs before DB write
- [ ] Unit tests cover edge cases: empty arrays, null RPE, pain during exercise, empty profile
- [ ] Phase snapshot generates without manual intervention
- [ ] Full offline operation (no cloud except optional vision API for `visual_delta`)
