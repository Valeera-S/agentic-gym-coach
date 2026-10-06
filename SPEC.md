DESIGN SPECIFICATION: Agentic Gym Coach (v2)

POSITIONING
A general-purpose, local-first gym coach engine. Any user, any goal. Professional doctrine comes exclusively from two vendored book-skills (procedural disclosure, no LLM-invented physiology or nutrition science); the user's goals, constraints, and history are validated data (DuckDB), never prompt text. A deterministic safety layer gates every exercise suggestion against recorded injuries.

1. KNOWLEDGE ARCHITECTURE
The only professional knowledge sources (v1 documents were removed as unsourced):
- docs/knowledge/helms-training-pyramid/ — Muscle & Strength Pyramid: Training (2nd ed.)
- docs/knowledge/helms-nutrition-pyramid/ — Muscle & Strength Nutrition Pyramid (v1.0)

Precedence: (1) deterministic safety layer, (2) vendored skills (doctrine), (3) mechanics docs. Each skill's SKILL.md is a topic router; agents load at most one knowledge file per turn (procedural disclosure).

2. DATA ARCHITECTURE
Local-first embedded stack: DuckDB (data/gym_coach.duckdb), Polars (never Pandas), Pydantic V2 at every boundary, Alembic migrations (never hand-edit schema).

2.1 Tables (post migration 0005)
- bodyweight_log — id UUID PK, date DATE, weight_kg DOUBLE (0 < kg <= 400), weight_entered DOUBLE + weight_unit VARCHAR ('kg' | 'lb', as entered; 1 lb = 0.45359237 kg), condition VARCHAR NOT NULL (vocab: WeighCondition in models/bodyweight.py — morning_fasted | fed | post_workout | unknown; required on input), scale VARCHAR, notes TEXT, created_at (0005; several readings per date allowed; never mixed across conditions in an average; downgrade refuses while rows exist; never auto-written into profile.bodyweight_kg)
- sessions — date, phase VARCHAR (vocab: PhaseType enum in models/enums.py), pre_recovery_score, exercises STRUCT(name, muscle_group VARCHAR (vocab: MuscleGroup), sets, reps FLOAT[], rpe FLOAT[], weight_kg DOUBLE[], tempo, form_quality, pain_flag, notes, raw_name, muscle_source, load_type, entered_weight DOUBLE[], entered_unit)[], post_feedback, kind VARCHAR NOT NULL DEFAULT 'training' ('training' | 'habit' — a standing daily item done outside training: it counts toward volume but is excluded from recovery's training-load inputs (pain flagged in a habit still counts), the session-gap / staleness signal, deload block state and the current-phase resolver). A NULL per-set array (reps / rpe / weight_kg) reads as "every set unrecorded" (padded to `sets`, like the model pads empty arrays). The per-exercise fields after `notes` were added by 0003 and are NULL on pre-0003 rows ("unknown"). `weight_kg` is the reading on the implement — per hand (dumbbells), per side (twin-stack cable), machine stack reading, or total plate load (a Smith machine's own bar weight is unknowable and excluded); `load_type` (per_hand | per_side | total | machine_stack | bodyweight, NULL = unknown) records which. A caller may log `weight` + `unit` ('kg' | 'lb') instead of `weight_kg`: weight_kg = weight × 0.45359237 for lb, and the entered values + unit are stored in `entered_weight` / `entered_unit` for read-back.
- injury_status — location VARCHAR (vocab: PainLocation enum), status, severity 0–10, contraindicated_exercises[], safe_alternatives[]
- phase_snapshots — snapshot_date PK, phase VARCHAR, body_weight_kg (7-day mean of morning_fasted bodyweight_log readings ending at the snapshot date; NULL when none — no fallback to other conditions or the profile), waist_cm, specialization_lifts JSON, tendon_status_summary JSON, key_insight, next_phase_adjustment
- decision_log — event_type, trigger_signal, reasoning_chain, alternative_rejected, future_validation_tag, payload JSON (audit trail; `payload` added by 0004 holds the complete pre-change session row — plus the post-change row for amends — so an amended/deleted session can be restored from the audit entry alone)
- user_profiles — id UUID PK, updated_at TIMESTAMPTZ, payload JSON (append-only history; latest row = current UserProfile; goal changes audited)
- memory_notes — id UUID PK, created_at, kind, text, tags[] (Tier 3; manual-save only; substring/tag search)

2.2 Vocabulary policy
DB stores phase/location and the per-exercise muscle_group as VARCHAR (muscle_group since 0003, which also remapped the retired `upper_chest` to `chest`); models/enums.py is the controlled vocabulary, enforced by Pydantic at the boundary. Extending a vocabulary = edit the enum, no migration. (Supersedes v1 SPEC §1.2 "enforce via SQL ENUM".)

2.3 Volume semantics (Training ch03)
Volume currency = effective hard sets per muscle per week: sets × form_mult (form_quality < 3 ⇒ 0.5), primary + secondary contributions 1:1 (models/exercise_catalog.SECONDARY_OVERLAP — DERIVED from the Training ch03 counting chart via models/doctrine_ch03.py, never hand-listed), each muscle at most once per set, `unclassified` credits nothing, bodyweight sets count. Exercise names are identities: a different implement or machine is a different identity; generic names ("Bench Press", "Fly", ...) stay valid and keep historical rows counting. Tonnage is reference detail only: total external load, so per_hand / per_side readings count ×2; sets with unknown load_type are counted as read and reported (`load_type_unknown_sets`). est_1rm from reps ≤ 6 sets only ("~5RM or heavier", ch04).

3. SKILL INVENTORY (deterministic Python, no LLM logic)
- session_logger.log_session(SessionInput) -> LogConfirmation  (<50ms); prepare_session() — the single validation/canonicalization path (shared with amend); records raw_name, muscle_source (catalog | keyword | caller | unclassified), load_type (catalog default when not given), entered weight + unit
- sessions.get_session_detail(session_id | date) -> list[SessionDetail]; list_sessions(limit) (lean, + needs_review count); amend_session(SessionAmendInput) / delete_session(id) -> SessionChange (audited snapshots; amend exercises are input fields only — read-back keys rejected — each with exactly one of `index` (the stored exercise it restates/edits, as numbered in SessionDetail) or `new: true`, unreferenced stored exercises removed and every one reported in the reply's `removed_exercises` (nameless entries of older rows are shown by session detail with their index and can be referenced); a restatement keeps the stored raw_name, weight + unit, load_type and muscle_source exactly (NULLs included — except that a pre-0003 stored name the catalog now maps to another identity becomes the raw_name, as that is what was typed, and the name is canonicalized); an omitted muscle_group is re-derived; `confirm_muscle` records the caller's; a muscle equal to the replaced exercise's (a caller-set one too, unless `confirm_muscle`) or a load_type copied onto a renamed exercise is not applied, and flagged `amend_not_applied` where that changes the result); restore_snapshot(audit_id) — recovery helper, itself audited as a `session_restore` entry whose payload holds the state it overwrote (so a restore is reversible)
- exercise_catalog.classify()/canonicalize() — identity, keyword guess (HEURISTIC, flagged), or `unclassified`; doctrine_ch03.chart_credit()/chart_primaries() — the only source of credited muscles
- safety_gate.check_exercise_safety(str) -> SafetyResult  (<10ms, deterministic)
- injuries.list_injuries()/get_active_injuries()/seed_injury() — single owner of injury_status; write-time vocabulary validation + name canonicalization; seed rejects non-list or blank name lists and safe_alternatives the same row bans; the gate returns only alternatives that pass all current bans; the latest row per location is its current state (resolved lifts bans, older rows are history; list_injuries flags `is_current`) and drives the gate and tendon_summary
- recovery.compute_recovery_score(date) -> RecoveryScore  (<30ms)
- trend_analysis.get_specialization_trend(muscle, window_days, end_date) -> TrendReport (direction: est-1RM per exercise identity (chart primaries only, heavy sets in both halves; halves = first floor(n/2) training sessions by date, created_at, id) when any identity votes, else per-exercise performance — double progression; detail.direction_basis / identity_directions); hard_sets_by_muscle(start, end)  (<100ms on 10K rows); `window_days=N` = exactly N calendar days ending at `end_date` inclusive (`trend_analysis.window_start`, also the snapshot's 28-day anchor)
- metrics — shared Epley/est-1RM helpers (single definition)
- phase.current_phase()/phase_for_logging() — single phase resolver: current phase = the phase of the most recent training session (date, created_at, id; habits never decide); the latest phase_snapshots row only when no training session exists
- snapshot.generate_phase_snapshot() -> PhaseSnapshot (incl. computed block_state + session_gap); session_gap(today) -> weeks since the last training session (habits excluded) + staleness verdict (threshold REASSESSMENT_GAP_WEEKS, a labeled heuristic)  (<200ms)
- bodyweight.log_bodyweight(BodyweightInput) -> BodyweightReading; list_readings(start, end); summarize(start, end) -> mean + count per condition (never blended); bodyweight_history(window_days, end) -> readings + last-7-days and window summaries (window_start semantics: N calendar days ending at end, inclusive); fasted_mean_7d(end) feeds the snapshot
- intake.assess_intake() -> IntakeReport — standardized bucket-list scan (models/intake.py INTAKE_CHECKLIST): collected vs missing per field, soft per-domain readiness gates; read-only
- profile.get_profile()/set_profile()/derive_priority_muscles()
- memory.add_note()/search_notes()  (Tier 3, manual-save policy)
- visual_delta.compare_photos(a, b) -> VisualDelta  (stub; optional vision API)

4. SURFACES
- CLI: python coach_tools.py <cmd> '<json>' (JSON stdout; {"error":...} + exit 1)
- MCP: mcp_server.py (stdio; 18 coach tools + coach_doctrine) — the cross-runtime tool surface. Each tool call opens the DB and releases it on return (an idle server never locks the file).
- Persona: docs/COACH_PROMPT.md (canonical; renderable into native agent files via scripts/sync_adapters.py)
- See docs/adapters.md. (The v1/v2 opencode native adapter was removed — MCP is the single tool surface.)

5. INVARIANTS
- Skills are pure deterministic code; reasoning happens in the orchestrator/agent.
- Safety gate result is binding: safe=false ⇒ never suggest the exercise.
- Intake gates are soft: a not-ready domain gets no volunteered plans, and a provisional plan (explicit user insistence only) must name every missing field and its limitation. There is one intake flow — no separate onboarding/re-assessment modes.
- The session-gap staleness threshold is a labeled heuristic (not book-sourced), configurable only in skills/snapshot.py (REASSESSMENT_GAP_WEEKS).
- Tier 3 memory writes require an explicit user command; never auto-write.
- Cite retrieved values; missing data = "I don't have that data." Never fabricate.
- Every plan modification (incl. goal changes) lands in decision_log. Goal-change rule (`skills/profile.py`): goals are compared on their full content (kind, physique_target, target_muscles, metric, deadline, notes) as a multiset; any difference writes one `goal_change` entry (goals added / removed, and for a goal of the same kind its changed fields from -> to), no difference writes none, and pure reordering (of goals or of a goal's target_muscles) is no change. The first profile ever stored has no prior goals, so it writes no entry.
- Full offline operation (no cloud except optional vision API for visual_delta).
- Tests never touch production data (conftest.py env redirect). Single-process pytest.
