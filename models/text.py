"""Input-boundary checks for free text (P39, P40).

Applies to what a caller sends: exercise names, notes, tempo, post_feedback, the
bodyweight scale / notes, injury ban names, memory text / tags / queries, profile
strings and the safety_check name (P66). Readers keep tolerating whatever is
already stored.
"""

from __future__ import annotations

import unicodedata

MAX_NAME_LEN = 200
# Free text (notes, post_feedback, bodyweight scale/notes) may carry line breaks:
# newline, carriage return (Windows CRLF line endings) and tab. Names and tempo
# are single-line identifiers and allow no control character at all (P57).
FREE_TEXT_CONTROL = (chr(10), chr(13), chr(9))


def clean_text(v: str | None, what: str, *, allowed: tuple[str, ...] = FREE_TEXT_CONTROL) -> str | None:
    """Reject text DuckDB or a client could not carry: a lone surrogate (cannot
    be encoded as UTF-8) and control characters other than `allowed` (default:
    newline, carriage return, tab; pass `allowed=()` for a single-line field)."""
    if v is None:
        return v
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{what} contains a character that cannot be encoded as UTF-8 "
                         "(a lone surrogate); resend it as valid text") from None
    for ch in v:
        if ch not in allowed and unicodedata.category(ch) == "Cc":
            ok = ("only newline, carriage return and tab are allowed" if allowed
                  else "no control characters are allowed in this field")
            raise ValueError(f"{what} contains a control character (U+{ord(ch):04X}); {ok}")
    return v


def clean_name(v: str) -> str:
    """An exercise name: non-blank after strip, at most MAX_NAME_LEN characters."""
    if len(v) > MAX_NAME_LEN:
        raise ValueError(f"exercise name is too long ({len(v)} characters; "
                         f"at most {MAX_NAME_LEN})")
    clean_text(v, "exercise name", allowed=())
    if not v.strip():
        raise ValueError("exercise name must not be blank")
    return v


MAX_LABEL_LEN = 40


def clean_label(v: str | None) -> str | None:
    """A session label ("back day"): optional; when given, single-line, not
    blank, at most MAX_LABEL_LEN characters. Stored exactly as entered —
    matching normalizes through exercise_catalog.lookup_key."""
    if v is None:
        return v
    if len(v) > MAX_LABEL_LEN:
        raise ValueError(f"label is too long ({len(v)} characters; at most {MAX_LABEL_LEN})")
    clean_text(v, "label", allowed=())
    if not v.strip():
        raise ValueError("label must not be blank (leave it out instead)")
    return v


def clean_ident(v: str, what: str) -> str:
    """A short single-line identifier (a tag, a ban or alternative name, a profile
    short string): valid UTF-8 and no control character at all (P66). Blankness
    is the caller's rule."""
    clean_text(v, what, allowed=())
    return v


def clean_ident_list(values: list[str], what: str, *, allow_blank: bool = True) -> list[str]:
    """clean_ident on every item; `allow_blank=False` also rejects a blank item (P67)."""
    for item in values:
        clean_ident(item, what)
        if not allow_blank and not item.strip():
            raise ValueError(f"{what} must not contain a blank item")
    return values


def is_stored_read(info) -> bool:
    """True when a model is being rebuilt from what is already stored
    (`context={"stored": True}`): readers keep tolerating old values."""
    return bool(info is not None and info.context and info.context.get("stored"))
