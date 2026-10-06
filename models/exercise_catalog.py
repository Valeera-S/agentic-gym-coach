"""Exercise catalog — exercise IDENTITIES, their aliases, and chart-derived muscles.

A canonical name here is an exercise identity, not a counting bucket: a
different implement or machine is a different identity, because their loads
are not comparable ("Dumbbell Fly" vs "Cable Fly", "Smith Incline Press" vs
"Barbell Incline Bench Press"). Aliases are only true synonyms — spelling, case,
grip/width wording of the SAME implement.

Muscles are never hand-listed. Each identity's movement pattern lives in
models/doctrine_ch03.py (EXERCISE_PATTERN); the credited muscles follow from
that pattern's row of the Training ch03 counting chart, minus the chart's own
conditionals recorded there as documented exceptions. Isolation-row exercises
name their single target muscle. `SECONDARY_OVERLAP` (primary AND secondary
count 1:1 toward weekly hard sets, ch03) is derived, not written.

Generic identities ("Bench Press", "Fly", "Shoulder Press", "Row", ...) are
names that do not say which implement was used. They are the names earlier
versions of this catalog stored for merged implements, so historical rows keep
resolving to a pattern and keep counting. They stay valid input, but carry no
load_type default: the implement is unknown, so nothing is assumed about how
the weight was measured.

`load_type` is a DEFAULT only (the caller can override it per log):
    per_hand       dumbbells — the reading is one hand's load, both hands work
    per_side       twin-stack cable — the reading is one side's stack
    total          barbell / plate-loaded — the whole external load (a Smith
                   machine's bar weight is unknowable and not included)
    machine_stack  selectorized machine or single cable stack reading
    bodyweight     no external load recorded

Unknown names fall back to keyword matching (a guessed primary, flagged for
review), and a name with no keyword is `unclassified` — still loggable, but
credited to no muscle (see classify()). Ingestion never blocks on an unknown.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .doctrine_ch03 import EXERCISE_PATTERN, chart_credit, chart_primaries
from .enums import LoadType, MuscleGroup, MuscleSource

PH, PS, TOT, MS, BW = (LoadType.per_hand, LoadType.per_side, LoadType.total,
                       LoadType.machine_stack, LoadType.bodyweight)
M = MuscleGroup


@dataclass(frozen=True)
class Exercise:
    """One exercise identity. Muscles come from its ch03 pattern, not from here."""

    name: str
    aliases: tuple[str, ...] = ()
    load_type: LoadType | None = None  # default only; None = implement unspecified
    target: MuscleGroup | None = None  # isolation-row exercises only
    generic: bool = False              # name does not say which implement
    # Generic identities only: the specific identities the name may stand for.
    # The safety gate treats a ban on any of them as covering the generic name,
    # and a ban on the generic name as covering all of them (fail-closed).
    variants: tuple[str, ...] = ()


_ENTRIES: tuple[Exercise, ...] = (
    # --- horizontal push ------------------------------------------------------
    Exercise("Bench Press", ("Flat Bench Press", "Flat Bench"), generic=True,
             variants=("Barbell Bench Press", "Dumbbell Bench Press", "Smith Bench Press",
                       "Machine Chest Press", "Close-Grip Bench Press")),
    Exercise("Barbell Bench Press", ("Flat Barbell Bench Press", "Barbell Flat Bench Press",
                                     "BB Bench Press"), TOT),
    Exercise("Dumbbell Bench Press", ("Flat Dumbbell Press", "Flat Dumbbell Bench Press",
                                      "Dumbbell Flat Bench Press", "DB Bench Press"), PH),
    Exercise("Smith Bench Press", ("Smith Machine Bench Press", "Smith Flat Bench Press"), TOT),
    Exercise("Machine Chest Press", ("Chest Press", "Chest Press Machine",
                                     "Seated Chest Press"), MS),
    Exercise("Close-Grip Bench Press", ("Close Grip Bench", "CGBP"), TOT),
    Exercise("Incline Bench Press", ("Incline Press",), generic=True,
             variants=("Barbell Incline Bench Press", "Dumbbell Incline Press",
                       "Smith Incline Press", "Machine Incline Press")),
    Exercise("Barbell Incline Bench Press", ("Incline Barbell Bench Press",
                                             "Barbell Incline Press"), TOT),
    Exercise("Dumbbell Incline Press", ("Incline Dumbbell Press", "Dumbbell Incline Bench Press",
                                        "Incline DB Press"), PH),
    Exercise("Smith Incline Press", ("Smith Machine Incline Press",
                                     "Smith Incline Bench Press"), TOT),
    Exercise("Machine Incline Press", ("Incline Chest Press Machine",), MS),
    Exercise("Decline Push-Up", ("Decline Pushup", "Feet-Elevated Push-Up"), BW),
    Exercise("Dip", ("Dips", "Parallel Bar Dip"), BW),
    # --- vertical push --------------------------------------------------------
    Exercise("Shoulder Press", (), generic=True,
             variants=("Barbell Overhead Press", "Dumbbell Shoulder Press",
                       "Machine Shoulder Press")),
    Exercise("Barbell Overhead Press", ("Overhead Press", "OHP", "Strict Press",
                                        "Barbell Shoulder Press", "Military Press"), TOT),
    Exercise("Dumbbell Shoulder Press", ("Dumbbell Overhead Press", "DB Shoulder Press",
                                         "Seated Dumbbell Shoulder Press"), PH),
    Exercise("Machine Shoulder Press", ("Shoulder Press Machine",), MS),
    # --- fly ------------------------------------------------------------------
    Exercise("Fly", ("Chest Fly",), generic=True,
             variants=("Dumbbell Fly", "Cable Fly", "Machine Fly")),
    Exercise("Dumbbell Fly", ("Dumbbell Chest Fly", "Flat Dumbbell Fly", "DB Fly"), PH),
    Exercise("Cable Fly", ("Cable Crossover", "Cable Crossover Fly", "Cable Chest Fly"), PS),
    Exercise("Machine Fly", ("Pec Deck", "Pec Deck Fly", "Machine Chest Fly"), MS),
    # --- vertical pull --------------------------------------------------------
    Exercise("Pull-Up", ("Pullup", "Close Grip Pull-Up"), BW),
    Exercise("Lat Pulldown", ("Pulldown", "Cable Pulldown", "Wide Grip Pulldown",
                              "Neutral Grip Pulldown", "Lat Pull-Down"), MS),
    # A converging-arm (iso-lateral) pulldown machine: a different machine from
    # the cable-bar "Lat Pulldown", so its loads are a different identity.
    Exercise("Machine Lat Pulldown", ("Converging Lat Pulldown",
                                      "Iso-Lateral Lat Pulldown"), MS),
    # --- pullover / lat pushdown -----------------------------------------------
    Exercise("Straight Arm Pulldown", ("Straight Arm Lat Pulldown",), MS),
    # --- horizontal pull ------------------------------------------------------
    Exercise("Row", ("Wide Grip Row",), generic=True,
             variants=("Pendlay Row", "Cable Row", "Machine Row", "Reverse Row",
                       "Chest-Supported Dumbbell Row")),
    Exercise("Pendlay Row", ("Penlay Row",), TOT),
    Exercise("Cable Row", ("Seated Cable Row", "Neutral Grip Cable Row",
                           "Wide Grip Cable Row"), MS),
    Exercise("Machine Row", ("Seated Machine Row",), MS),
    # Prone on a ~45 degree bench; Training ch09's accessory menu names
    # chest-supported rows among the lumbar-sparing rows.
    Exercise("Chest-Supported Dumbbell Row", ("Chest-Supported DB Row", "Incline Dumbbell Row",
                                              "Incline Bench Dumbbell Row"), PH),
    Exercise("Reverse Row", ("Wide Grip Reverse Row",)),
    # --- squat ----------------------------------------------------------------
    Exercise("Squat", (), generic=True,
             variants=("Bulgarian Split Squat", "Split Squat", "Bodyweight Squat")),
    Exercise("Bulgarian Split Squat", ()),
    Exercise("Split Squat", ()),
    Exercise("Bodyweight Squat", ("Air Squat", "BW Squat"), BW),
    Exercise("Leg Press", (), TOT),
    # --- hip hinge ------------------------------------------------------------
    Exercise("Romanian Deadlift", ("RDL",)),
    # --- horizontal hip extension ---------------------------------------------
    Exercise("Hip Thrust", (), generic=True, variants=("Barbell Hip Thrust",)),
    Exercise("Barbell Hip Thrust", (), TOT),
    Exercise("Glute Bridge", ("Bridge",)),
    # --- isolation (target muscle only) ---------------------------------------
    Exercise("Lateral Raise", (), target=M.side_delt, generic=True,
             variants=("Dumbbell Lateral Raise", "Cable Lateral Raise",
                       "Machine Lateral Raise")),
    Exercise("Dumbbell Lateral Raise", ("DB Lateral Raise", "Reverse Grip Lateral Raise",
                                        "Reverse-Grip Dumbbell Lateral Raise",
                                        "Supinated Lateral Raise"), PH, M.side_delt),
    Exercise("Cable Lateral Raise", (), MS, M.side_delt),
    Exercise("Machine Lateral Raise", (), MS, M.side_delt),
    Exercise("Reverse Fly", ("Rear Delt Fly",), target=M.rear_delt, generic=True,
             variants=("Machine Reverse Fly",)),
    Exercise("Machine Reverse Fly", ("Reverse Pec Deck",), MS, M.rear_delt),
    # HEURISTIC — see doctrine_ch03.EXERCISE_PATTERN: face pulls are not in the book.
    Exercise("Face Pull", ("Cable Face Pull", "Rope Face Pull"), MS, M.rear_delt),
    Exercise("Overhead Tricep Extension", ("Overhead Triceps Extension",), target=M.triceps),
    Exercise("Tricep Pushdown", ("Triceps Pushdown",), MS, M.triceps),
    Exercise("Skull Crusher", (), target=M.triceps, generic=True,
             variants=("Dumbbell Skull Crusher",)),
    Exercise("Dumbbell Skull Crusher", (), PH, M.triceps),
    Exercise("Hammer Curl", (), PH, M.biceps),
    Exercise("Bay Curl", ("Bayesian Curl",), MS, M.biceps),
    Exercise("Curl", (), target=M.biceps, generic=True,
             variants=("Dumbbell Curl", "Hammer Curl", "Bay Curl")),
    Exercise("Dumbbell Curl", ("DB Curl",), PH, M.biceps),
    Exercise("Leg Extension", (), MS, M.quads),
    Exercise("Leg Curl", (), MS, M.hamstrings),
    Exercise("Single-Leg Curl", (), MS, M.hamstrings),
    Exercise("Cable Kickback", ("Cable Glute Kickback",), MS, M.glutes),
    Exercise("Calf Raise", (), target=M.calves, generic=True, variants=("Smith Calf Raise",)),
    Exercise("Smith Calf Raise", (), TOT, M.calves),
    Exercise("Crunch", (), BW, M.core),
    Exercise("Machine Crunch", (), MS, M.core),
    Exercise("Hanging Leg Raise", (), BW, M.core),
    Exercise("Leg Raise", ("Lying Leg Raise",), BW, M.core),
)

CATALOG: dict[str, Exercise] = {e.name: e for e in _ENTRIES}


def lookup_key(name: str) -> str:
    """Normalize a name for catalog lookup and ban matching.

    Steps: Unicode NFKC (fullwidth/compatibility forms -> plain), casefold,
    drop invisible format characters (category Cf: soft hyphen, ZWJ/ZWNJ, BOM,
    ... — except U+200B ZERO WIDTH SPACE, which is read as a word break, since
    pasted text uses it where a space was), then every character that is not
    alphanumeric (str.isalnum, Unicode-aware so CJK names survive) is a space
    — hyphens, underscores, punctuation, NBSP — and whitespace runs collapse
    ("chest-supported" == "chest supported" == "Chest Supported.").
    The safety gate normalizes stored ban entries with this same function
    (skills/injuries.py); both sides must normalize identically or
    canonicalization and the ban match stop composing — and any spelling that
    slips through is a banned exercise reported safe (adversarial F1; P24;
    P44). This is the ONLY normalization site — never inline a variant of it.
    Differently-spelled-but-equal names are one key, so never list them as
    separate aliases (the catalog build rejects a name claimed twice)."""
    text = unicodedata.normalize("NFKC", name).casefold().replace("​", " ")
    text = "".join(c if c.isalnum() else (" " if unicodedata.category(c) != "Cf" else "")
                   for c in text)
    return " ".join(text.split())


def _derive() -> tuple[dict[str, MuscleGroup], dict[str, list[MuscleGroup]]]:
    primary: dict[str, MuscleGroup] = {}
    secondary: dict[str, list[MuscleGroup]] = {}
    order = list(MuscleGroup)
    for e in _ENTRIES:
        p, credited = chart_credit(e.name, e.target)
        primary[e.name] = p
        secondary[e.name] = sorted(credited - {p}, key=order.index)
    return primary, secondary


# Primary muscle (stored in sessions.exercises.muscle_group) and the overlap
# credits, both derived from the chart — Helms ch03: an exercise's sets count
# 1:1 toward its secondary muscles as well as its primary. Analytics-only: the
# sessions table keeps storing the single primary group.
PRIMARY, SECONDARY_OVERLAP = _derive()

# Keyed by lookup_key so a name's casing never decides whether it maps.
_ALIAS_TO_CANONICAL: dict[str, tuple[str, MuscleGroup]] = {}
for _e in _ENTRIES:
    for _n in (_e.name, *_e.aliases):
        _k = lookup_key(_n)
        if _k in _ALIAS_TO_CANONICAL:
            raise ValueError(f"catalog name/alias {_n!r} is claimed twice")
        _ALIAS_TO_CANONICAL[_k] = (_e.name, PRIMARY[_e.name])


def _catalog_hit(key: str) -> tuple[str, MuscleGroup] | None:
    """Catalog lookup by normalized key; a plain plural ("...s" / "...es" /
    "...ies") of a catalog name or alias resolves to it too."""
    hit = _ALIAS_TO_CANONICAL.get(key)
    if hit is None and key.endswith("ies") and len(key) > 5:
        hit = _ALIAS_TO_CANONICAL.get(key[:-3] + "y")
    if hit is None:
        for suffix in ("es", "s"):
            if key.endswith(suffix) and len(key) > len(suffix) + 2:
                hit = _ALIAS_TO_CANONICAL.get(key[: -len(suffix)])
                if hit:
                    break
    return hit


def resolve_name(name: str) -> str | None:
    """Canonical identity for a catalog name or alias (any case); None if unknown.

    Analytics resolve STORED names through this too, so a row stored under a
    name that has since become an alias still credits the right muscles.
    """
    hit = _catalog_hit(lookup_key(name))
    return hit[0] if hit else None


def credited_muscles(name: str, stored_primary: str | None) -> set[str]:
    """Every muscle one logged set of `name` credits — each at most once.

    The identity's full chart credit (derived primary + overlap) plus the
    stored primary, as a set. The stored primary may differ from the derived
    one — a caller override, or a row stored under an older vocabulary (a
    pre-rebuild "Shoulder Press" row stored side_delt) — and must neither
    remove a chart muscle nor be counted twice.
    """
    canon = resolve_name(name)
    out = {m.value for m in _FULL_CREDIT.get(canon, ())} if canon else set()
    if stored_primary:
        out.add(stored_primary)
    # the sentinel is not a muscle: its sets stay logged but credit nothing
    out.discard(MuscleGroup.unclassified.value)
    return out


def default_load_type(name: str) -> LoadType | None:
    canon = resolve_name(name)
    return CATALOG[canon].load_type if canon else None


# The chart's primary set per identity — which exercises decide a muscle's
# progression direction (overlap credit counts toward volume only).
CHART_PRIMARIES: dict[str, frozenset[MuscleGroup]] = {
    e.name: chart_primaries(e.name, e.target) for e in _ENTRIES
}


def progression_muscles(name: str, stored_primary: str | None) -> set[str]:
    """Muscles whose progression a logged exercise speaks for: its identity's
    chart primaries; for a name the catalog does not know, the stored primary
    (the only muscle it is credited to)."""
    canon = resolve_name(name)
    if canon:
        return {m.value for m in CHART_PRIMARIES[canon]}
    if stored_primary and stored_primary != MuscleGroup.unclassified.value:
        return {stored_primary}
    return set()


_FULL_CREDIT: dict[str, frozenset[MuscleGroup]] = {
    name: frozenset({PRIMARY[name], *SECONDARY_OVERLAP[name]}) for name in PRIMARY
}

# Reverse maps: muscle -> canonical names that credit it (secondarily / at all).
_SECONDARY_NAMES: dict[MuscleGroup, list[str]] = {}
for _canon, _secondaries in SECONDARY_OVERLAP.items():
    for _m in _secondaries:
        _SECONDARY_NAMES.setdefault(_m, []).append(_canon)
_CREDITING_NAMES: dict[MuscleGroup, list[str]] = {}
for _canon, _muscles in _FULL_CREDIT.items():
    for _m in _muscles:
        _CREDITING_NAMES.setdefault(_m, []).append(_canon)


def secondary_exercises(muscle: MuscleGroup) -> list[str]:
    """Canonical names whose sets also count toward `muscle` (overlap)."""
    return _SECONDARY_NAMES.get(muscle, [])


def exercises_crediting(muscle: MuscleGroup) -> list[str]:
    """Canonical names whose chart credit includes `muscle` (primary or overlap)."""
    return _CREDITING_NAMES.get(muscle, [])


# --- legacy identities ------------------------------------------------------
# The catalog as of d0bc2ef, frozen: canonical name -> the aliases that merged
# into it. Several of those aliases are now distinct identities. Rows and
# injury bans stored before this rebuild carry the OLD canonical name, which
# therefore stands for its whole family. Never edit — it is history.
LEGACY_ALIASES: dict[str, tuple[str, ...]] = {
    "Incline Bench Press": ("Incline Press", "Machine Incline Press",
                            "Dumbbell Incline Press", "Incline Dumbbell Press"),
    "Decline Push-Up": (),
    "Bench Press": ("Barbell Bench Press", "Flat Bench Press", "Flat Bench",
                    "Dumbbell Bench Press", "Chest Press", "Machine Chest Press"),
    "Fly": ("Cable Fly", "Dumbbell Fly", "Chest Fly", "Machine Fly", "Pec Deck"),
    "Shoulder Press": ("Barbell Shoulder Press", "Machine Shoulder Press",
                       "Dumbbell Shoulder Press", "Strict Press", "Overhead Press"),
    "Lateral Raise": ("Cable Lateral Raise", "Machine Lateral Raise", "Dumbbell Lateral Raise"),
    "Reverse Fly": ("Machine Reverse Fly",),
    "Face Pull": ("Cable Face Pull", "Rope Face Pull"),
    "Pull-Up": ("Pull Up", "Close Grip Pull-Up"),
    "Straight Arm Pulldown": (),
    "Lat Pulldown": ("Pulldown", "Cable Pulldown", "Wide Grip Pulldown", "Neutral Grip Pulldown"),
    "Row": ("Penlay Row", "Pendlay Row", "Cable Row", "Neutral Grip Cable Row",
            "Wide Grip Cable Row", "Wide Grip Row", "Machine Row",
            "Reverse Row", "Wide Grip Reverse Row"),
    "Dip": (),
    "Overhead Tricep Extension": (),
    "Tricep Pushdown": (),
    "Skull Crusher": ("Dumbbell Skull Crusher",),
    "Hammer Curl": (),
    "Bay Curl": (),
    "Dumbbell Curl": ("Curl",),
    "Squat": ("Bulgarian Split Squat", "Split Squat"),
    "Leg Extension": (),
    "Leg Press": (),
    "Romanian Deadlift": (),
    "Leg Curl": ("Single-Leg Curl",),
    "Hip Thrust": ("Barbell Hip Thrust", "Glute Bridge", "Bridge"),
    "Cable Kickback": ("Kickback",),
    "Calf Raise": ("Smith Calf Raise",),
    "Crunch": ("Machine Crunch",),
    "Hanging Leg Raise": ("Leg Raise",),
}


def _canon_or_text(name: str) -> str:
    return resolve_name(name) or name.strip()


# Identities a legacy canonical name may ALSO have meant, beyond its frozen
# aliases: rows and bans stored as "Lat Pulldown" before the split were logged
# on whatever pulldown the user's gym has — the user's is the converging-arm
# machine. Fail-closed: such a legacy ban keeps blocking the machine identity.
LEGACY_EXTRA_COVERAGE: dict[str, tuple[str, ...]] = {
    "Lat Pulldown": ("Machine Lat Pulldown",),
}


def legacy_family(name: str) -> set[str]:
    """Current identities a name may stand for, itself included: the aliases a
    legacy canonical name used to merge, plus a generic identity's declared
    variants. Empty for a specific identity that was never a legacy name.
    Case/whitespace-insensitive, like every other catalog lookup."""
    canon = _canon_or_text(name)
    legacy = _LEGACY_BY_KEY.get(lookup_key(canon))
    aliases = (*LEGACY_ALIASES[legacy], *LEGACY_EXTRA_COVERAGE.get(legacy, ())) if legacy else None
    variants = CATALOG[canon].variants if canon in CATALOG else ()
    if aliases is None and not variants:
        return set()
    return {_canon_or_text(a) for a in (canon, *(aliases or ()), *variants)}


def ban_match_names(name: str) -> set[str]:
    """Names whose ban must block `name` — fail-closed across the identity split.

    - the identity itself;
    - every legacy name whose family contains it (a ban stored as "Bench Press"
      before the split still blocks "Dumbbell Bench Press");
    - if `name` is itself a legacy/generic name, its whole family (a query that
      does not say the implement may be any of them, so any of their bans block).
    All comparisons go through lookup_key: letter case never decides a ban.
    """
    canon = _canon_or_text(name)
    key = lookup_key(canon)
    names = {canon} | legacy_family(canon)
    names |= {cover for cover, keys in _FAMILY_KEYS.items() if key in keys}
    return names


_LEGACY_BY_KEY = {lookup_key(n): n for n in LEGACY_ALIASES}
# Every name that can cover others (legacy canonical names + generic
# identities) -> the lookup keys of its family.
_FAMILY_KEYS: dict[str, frozenset[str]] = {
    cover: frozenset(lookup_key(n) for n in legacy_family(cover))
    for cover in sorted(set(LEGACY_ALIASES) | {e.name for e in _ENTRIES if e.variants})
}
for _e in _ENTRIES:
    for _v in _e.variants:
        if _v not in CATALOG:
            raise ValueError(f"{_e.name!r} lists unknown variant {_v!r}")


# --- unknown names: keyword fallback -----------------------------------------
# HEURISTIC — none of this table is book doctrine. For a name the catalog does
# not know, the first matching rule guesses its PRIMARY muscle (the ch03
# chart's first-listed primary for that pattern, so a guess agrees with what
# the catalog would store); the non-chart lines (stop words, band walks,
# pull-aparts, ...) are reasoned guesses, and every hit is reported to the
# user as a guess (needs_review). Guesses credit only that primary (no
# overlap: the pattern itself is a guess).
# Keywords match whole words, with an optional plural; hyphens count as word
# breaks, so rules that a hyphenated prefix could trip ("iso-lateral") are
# written as specific phrases or guarded, never as a bare word. Regex gaps are
# bounded ({0,40}) so a pathological name cannot make matching slow.
#
# ORDER IS PRECEDENCE — specific before general. Notable pairs:
#   reverse/rear/bent-over ... fly|lateral|raise  before  fly, lateral
#   stop words (wrist curl, forearm, neck, row erg, sled, ...)  before  curl, row
#   straight-arm / lat pushdown    before  pushdown
#   pike / handstand push-up       before  push-up
#   reverse nordic (quads)         before  nordic (hamstrings)
#   tricep / dumbbell kickback     before  kickback
#   calf                           before  leg press
#   lunge / band walk / squat      before  bare "laterals"
#   hinge pulls (rack/block pull, pull-through)  before  pull
#   row                            before  chest / bench / incline  ("chest-supported row")
#   leg / ham / hamstring curl     before  curl
#   "walk"                         before  incline   ("incline walk")
#   crunch / sit-up / plank        before  decline / incline / bench  ("decline crunch")
#   lateral pulldown (lats)        before  bare "laterals"
#   glute-ham raise (hamstrings)   before  glute
# A rule may map to `unclassified`: the name is recognised as NOT strength work
# for any muscle in the vocabulary, so it credits nothing.

_GAP = r".{0,40}?"


def _kw(*words: str) -> str:
    """Whole-word alternatives, each with an optional plural."""
    return r"\b(?:" + "|".join(re.escape(lookup_key(w)) for w in words) + r")(?:e?s)?\b"


_KEYWORD_RULES: list[tuple[str, MuscleGroup]] = [
    # rear delts — reverse/rear/bent-over + a fly/raise word ("reverse grip" is
    # a grip, not a rear-delt movement)
    (rf"\b(?:reverse(?![- ]grip)|rear|bent[- ]over)\b{_GAP}\b(?:fl(?:y|ys|yes|ies)"
     r"|butterfl(?:y|ies)|crossovers?|laterals?|raises?)\b", M.rear_delt),
    (_kw("rear delt", "reverse pec", "face pull", "facepull", "pull-apart", "pull apart"),
     M.rear_delt),
    # not strength work for any vocabulary muscle — before curl / row / lateral
    (_kw("wrist curl", "forearm curl", "neck curl", "neck extension", "neck flexion",
         "neck harness", "jefferson curl", "lateral box jump", "lateral shuffle",
         "lateral bound", "box jump", "row erg", "rowing machine", "concept2", "erg", "sled",
         "treadmill", "elliptical", "stairmaster", "bike", "cycling", "jog", "run",
         "y raise", "y-raise", "shrug", "stretch"), M.unclassified),
    # ch03 "Pullover / lat pushdown" -> lats (before the triceps "pushdown")
    (r"\bstraight[- ]arm\b", M.lats),
    (_kw("lat pushdown", "pullover", "pull-over", "lateral pulldown", "lateral pull-down",
         "lateral pull down"), M.lats),
    # calves (before "leg press": "leg press calf raise")
    (_kw("calf", "calves"), M.calves),
    # delts
    (_kw("upright row"), M.side_delt),
    (_kw("pike push-up", "pike pushup", "pike push up", "handstand push-up",
         "handstand pushup", "handstand push up", "hspu"), M.front_delt),
    (_kw("lateral raise", "lat raise", "lateral delt raise", "lateral fly", "side raise",
         "side lateral"), M.side_delt),
    (_kw("front raise"), M.front_delt),
    # triceps (before kickback)
    (_kw("tricep", "skull crusher", "skullcrusher", "skull", "pushdown", "pressdown",
         "dip", "dumbbell kickback", "french press", "overhead extension",
         "overhead cable extension"), M.triceps),
    # reverse nordic is knee extension (quads) — before the hamstring nordic
    (_kw("reverse nordic"), M.quads),
    (_kw("leg curl", "ham curl", "hamstring curl", "nordic", "ghr", "glute ham raise",
         "glute-ham raise"), M.hamstrings),
    # quads, abs and glutes on the leg/hip patterns
    (_kw("leg extension", "leg ext", "knee extension", "leg press"), M.quads),
    (_kw("leg raise", "knee raise", "toes to bar", "ab wheel", "rollout", "russian twist",
         "crunch", "sit-up", "sit up", "situp", "plank"), M.core),
    (_kw("hip thrust", "hip thruster", "glute", "bridge", "kickback", "band walk",
         "monster walk", "kettlebell swing", "kb swing", "donkey kick"), M.glutes),
    (_kw("walk"), M.unclassified),  # after the band walks, before "incline" (incline walk)
    (_kw("lunge", "step-up", "step up", "squat", "squatting", "split squat", "bulgarian"),
     M.quads),
    # a bare "lateral(s)" is the side-delt raise — but never "iso-lateral"
    (r"(?<!iso-)(?<!iso )\blaterals?\b", M.side_delt),
    # hinge — ch03 hip hinge, first-listed primary: glutes (before "pull")
    (_kw("deadlift", "deadlifting", "rdl", "sldl", "good morning", "back extension",
         "hyperextension", "reverse hyper", "rack pull", "block pull", "pull-through",
         "pull through"),
     M.glutes),
    # horizontal pull — ch03 first-listed primary: lats (before chest/bench/incline)
    (_kw("row", "seated rowing", "cable rowing"), M.lats),
    # arms
    (_kw("curl"), M.biceps),
    # vertical pull
    (_kw("pulldown", "pull-down", "pull-up", "pullup", "pull up", "chin-up", "chinup",
         "chin up", "chin", "pull"), M.lats),
    # vertical push — ch03: anterior delts primary
    (_kw("shoulder press", "overhead press", "military press", "arnold press",
         "push press", "behind the neck press", "behind-the-neck press",
         "overhead dumbbell press"), M.front_delt),
    # chest (horizontal push / fly)
    (_kw("fly", "flye", "flies", "butterfly", "pec", "bench", "benching", "benchpress",
         "chest", "incline", "decline", "floor press", "flat db press", "hex press",
         "larsen press", "spoto press", "guillotine press", "pin press", "board press",
         "flat dumbbell press", "push-up", "pushup", "push up"), M.chest),
]

_KEYWORD_PATTERNS = [(re.compile(rx), mg) for rx, mg in _KEYWORD_RULES]


@dataclass(frozen=True)
class Classification:
    """How a raw exercise name was resolved."""

    name: str                  # catalog identity, or the trimmed raw name
    muscle_group: MuscleGroup  # primary muscle (`unclassified` if none)
    source: MuscleSource       # catalog | keyword | unclassified

    @property
    def needs_review(self) -> bool:
        return self.source is not MuscleSource.catalog


def classify(raw_name: str) -> Classification:
    """Catalog identity (any alias, any case), else a keyword guess, else
    `unclassified`. Never raises on an unknown name — ingestion never blocks."""
    key = raw_name.strip()
    hit = _catalog_hit(lookup_key(key))
    if hit:
        return Classification(hit[0], hit[1], MuscleSource.catalog)
    low = lookup_key(key)
    for pattern, mg in _KEYWORD_PATTERNS:
        if pattern.search(low):
            if mg is M.unclassified:  # recognised as no vocabulary muscle's work
                break
            return Classification(key, mg, MuscleSource.keyword)
    return Classification(key, M.unclassified, MuscleSource.unclassified)


def canonicalize(raw_name: str) -> tuple[str, MuscleGroup, bool]:
    """Return (canonical_name, muscle_group, needs_review) — see classify().

    needs_review is True for a keyword guess or an unclassified name, so the
    coach confirms the mapping with the user.
    """
    c = classify(raw_name)
    return c.name, c.muscle_group, c.needs_review
