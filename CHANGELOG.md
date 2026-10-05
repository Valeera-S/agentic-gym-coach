# Changelog

Notable changes to the Agentic Gym Coach. Format follows Keep-a-Changelog;
entries are dated (the repo's version markers live in AGENTS.md's
workspace-state line).

## v2.4 — 2026-10-05 · Data-correctness pass (fork `personal`)

Fixes recorded from two weeks of real use: the system logged wrong muscles,
lost the user's own numbers, could not read a session back or correct it,
and called progressing lifters "stalled".

### Added
- **`coach_session_detail {session_id | date}`** — any logged session read
  back exactly as entered: identity next to the raw name, sets/reps/rpe, the
  user's own weights and unit, load_type, muscle_source and a per-exercise
  `needs_review`. `coach_sessions` gains a `needs_review` count.
- **`coach_session_amend` / `coach_session_delete`** — corrections through a
  supported path. Amend re-validates like a fresh log and keeps the
  id; omitted fields keep their stored values, `clear` removes them
  explicitly. Amend exercises take the logging fields only (the read-back
  shape is rejected, naming the field). Each names what it amends:
  `index` (the stored exercise, numbered by `coach_session_detail`) or
  `new: true`; a restatement keeps exactly what was recorded (raw
  name, weight + unit as entered, load_type, how its muscle was decided —
  unknowns on older rows stay unknown, except that an older row's name the
  catalog has since learned as an alias is canonicalized and kept as the raw
  name, since that is what was typed). Omitting `muscle_group` re-derives
  it (the way to fix an old guess); `confirm_muscle` records the user's
  confirmation; a muscle not recorded as the user's own, or a load type, copied onto
  a renamed exercise is not applied, and flagged `amend_not_applied`
  where that changes the result. Both write the complete previous row to
  `decision_log.payload`
  (migration 0004) — restorable from the audit entry alone. The persona
  requires the user's explicit yes before either.
- **Units and load types** — log `weight` + `unit` ('kg' | 'lb'; 1 lb =
  0.45359237 kg exactly, entered values kept for read-back) and `load_type`
  (per_hand | per_side | total | machine_stack | bodyweight; catalog
  defaults). Tonnage counts per_hand / per_side twice.
- **Habit sessions** (`kind = habit`) — daily items that count toward volume
  without making recovery or the session gap think the user trained.
- **Book editions** on every `coach_doctrine` response (the nutrition book is
  flagged as the 2015 first edition).

### Changed
- **Exercise identities** — the catalog (65 identities, human-approved) treats
  a different implement as a different exercise; every credited muscle is
  derived from the Training ch03 counting chart, including its conditionals
  (middle delts on incline pushes only, triceps primary for close-grip and
  dips, erectors for free-weight squats only). KNOWN_DEVIATIONS is empty.
- **Muscle vocabulary** — `chest` replaces `upper_chest` (still accepted as
  input), plus `front_delt`, `erectors` and the `unclassified` sentinel;
  `core` now means abdominal work only. `muscle_group` is VARCHAR in the
  database (migration 0003) — extending the vocabulary needs no migration.
- **Unknown exercises** are `unclassified` (credited to no muscle) instead of
  silently counting as core; keyword guesses are ordered, word-boundary rules
  and are reported as guesses; `muscle_source` records how each muscle was
  decided.
- **Trend direction** — judged on per-exercise performance (Training ch04
  double progression) instead of set counts; unrecorded weights are unknown,
  never 0.
- **Connection lifecycle** — the DuckDB file is released after every tool
  call, so an idle MCP server no longer locks out alembic or the CLI.
- **alembic** honours `GYM_COACH_DUCKDB` and creates the data directory.
- Recovery declares that it excludes the query date; the uncapped Epley
  value is no longer reported.

### Docs
- Windows MCP registration (local-scope override of the POSIX `.mcp.json`),
  connection-lifecycle and migration notes, glossary terms for exercise
  identities, muscle_source, habit sessions and load types.

### Upgrading
Run `alembic upgrade head` (0003 + 0004). Stored `upper_chest` values become
`chest`; rows logged before 0003 have unknown provenance/load fields.
Historical entries that were guessed wrong can now be corrected with
`coach_session_amend` (with the user's confirmation).

## v2.3 — 2026-09-20 · Standardized intake assessment

The prompt-scripted onboarding gate is replaced by a **code-driven,
standardized intake** with exactly one flow: first-time and returning users
go through the same scan; they differ only in how much it finds. When a plan
is needed, `coach_intake_status` reports what the database already knows vs
what's missing; the coach cites stored values (asking "still accurate?"),
queries the user only for the missing ones, and does not volunteer plans for
a domain whose gating data is absent — producing a provisional plan that
names its limitations only on explicit user insistence.
(ADR 0001, 0002 · tickets 02 absorbed, 07–09 created)

### Added
- **The bucket list as data** — `models/intake.py::INTAKE_CHECKLIST`: 27
  fields, every one citing its Helms chapter or carrying an explicit
  HEURISTIC label (drift-guarded by tests). Deliberately excluded: meals
  (frequency is book-neutral, Nutrition ch05), social support (effect
  unpredictable), priority muscles (derived, never asked), sleep-as-static-
  field, food allergies/dislikes, budget/cooking, clinical screening,
  motivation scoring (ADR 0002).
- **`coach_intake_status`** (CLI + MCP): per-field collected/missing scan
  with book sources, `training_ready` / `nutrition_ready` soft gates, and
  `weeks_since_last_session`.
- **Schedule windows**: `weekly_availability` (`AvailabilityWindow`:
  weekday, start/end, venue) with validated, normalized 24h `HH:MM` times;
  the coach formats vague answers ("Fri after 7, school gym closes at 8")
  into windows and plans the week around them.
- **Staleness signal** (ticket 02, absorbed): `session_gap` on the phase
  snapshot + `weeks_since_last_session` on Tier-1 working memory; threshold
  `REASSESSMENT_GAP_WEEKS` = 8, labeled heuristic (the books don't cover
  detraining timelines).
- **~12 new profile fields**: life stress, concurrent sports, RPE
  calibration, tested maxes, diet-phase duration, tracking tier, eating-out
  frequency, alcohol, supplement notes, caffeine, and the Nutrition ch03
  insulin-resistance gates (asked only when a nutrition plan is due).
- **Docs**: ADR 0001 (intake architecture + soft gates), ADR 0002 (omitted
  and derived fields), `CONTEXT.md` glossary, research note on
  reconditioning (`docs/research/`), tickets 07–09.

### Changed
- COACH_PROMPT rewritten: "First interaction — onboarding gate" →
  "Standardized intake assessment" (scan → cite/confirm → ask missing →
  soft gates → provisional plans). No separate onboarding or re-assessment
  flows.
- Goals are treated as provisional expressions of an evolving vision —
  recorded as stated, refined at phase anchors; every revision goes through
  the audited `coach_profile_set` path (goal changes auto-audit).
- Priority muscles are derived from goal targets + observed weak points,
  never asked.
- `equipment_access` no longer defaults to `full_gym` — unanswered reads as
  missing. Empty `goals`/`weekly_availability` mean "never asked";
  `general_fitness` is the recorded way to say "no specific goal".
- Exercise likes/dislikes are non-blocking: they emerge through training.
- New persona rule 9: stored data are not permanent — confirm before a plan
  leans on them (per-field freshness cadence deliberately deferred:
  docs/IDEAS.md #7, ticket 08).

### Removed
- `meals_per_day` and `social_support` profile fields + `SocialSupport`
  enum (stored payloads remain valid — stale keys are ignored).

### Fixed
- AGENTS.md tool-count drift (12 → 13); "run onboarding" leftovers in the
  `.zcode` gym-coach skill and README; "onboarding gate" comment in
  `coach_tools.py`.
- `session_gap()` returns a typed `SessionGap` model (skills return
  Pydantic models, per AGENTS.md).
- Availability times: impossible times (`99:99`, `24:00`, `23:60`) rejected;
  human inputs normalized to one canonical `HH:MM` form.

### Deferred (ticketed)
- **07** plan feasibility review (revision round before delivering a plan)
- **08** returning-user handling — staleness policy for a rotted profile
- **09** reconditioning provenance relabel (backlog, low priority)
- **01** information shelf life · **IDEAS #7** per-field freshness cadence

**Tests:** 204 passing · 1 slow perf guard (environment-sensitive on some
machines; verified failing identically on the pre-change baseline).
