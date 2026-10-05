"""coach_doctrine — every response names its book + edition.

The nutrition knowledge base is the 2015 1st edition while the training one is
the current 2nd edition; a nutrition number must never reach the user detached
from that caveat.
"""

from __future__ import annotations

from pathlib import Path

import mcp_server

_KNOWLEDGE = Path(mcp_server.__file__).resolve().parent / "docs" / "knowledge"


def test_every_knowledge_base_has_a_recorded_edition():
    dirs = {p.name for p in _KNOWLEDGE.iterdir() if p.is_dir()}
    assert dirs == set(mcp_server._BOOK_SOURCES)


def test_recorded_editions_agree_with_the_vendored_skill_headers():
    """_BOOK_SOURCES is copied from each SKILL.md; this catches a re-vendor
    that changes the edition without updating the stamp."""
    training = (_KNOWLEDGE / "helms-training-pyramid" / "SKILL.md").read_text(encoding="utf-8")
    nutrition = (_KNOWLEDGE / "helms-nutrition-pyramid" / "SKILL.md").read_text(encoding="utf-8")
    assert "(2nd Edition)" in training
    assert "2nd edition" in mcp_server._BOOK_SOURCES["helms-training-pyramid"]
    assert "v1.0, 2015" in nutrition and "first edition" in nutrition
    assert "1st edition" in mcp_server._BOOK_SOURCES["helms-nutrition-pyramid"]


def test_chapter_response_is_stamped_with_book_and_edition():
    out = mcp_server.coach_doctrine(
        "helms-training-pyramid/chapters/ch03-level-2-volume-intensity-frequency.md")
    first = out.splitlines()[0]
    assert first.startswith("[Source: The Muscle & Strength Pyramid: Training, 2nd edition")


def test_nutrition_response_flags_the_first_edition():
    chapter = next((_KNOWLEDGE / "helms-nutrition-pyramid" / "chapters").glob("ch02-*.md"))
    out = mcp_server.coach_doctrine(f"helms-nutrition-pyramid/chapters/{chapter.name}")
    first = out.splitlines()[0]
    assert "1st edition" in first and "2015" in first
    assert "2nd edition may revise" in first


def test_documented_shorthand_topics_resolve_with_a_stamp():
    for topic, edition in (("training", "2nd edition"), ("nutrition", "1st edition")):
        out = mcp_server.coach_doctrine(topic)
        assert not out.startswith("error"), topic
        assert out.startswith("[Source: ") and edition in out.splitlines()[0]


def test_index_lists_the_edition_of_each_book():
    out = mcp_server.coach_doctrine("index")
    assert "2nd edition" in out and "1st edition (v1.0, 2015" in out


def test_stamp_does_not_count_against_the_truncation_budget():
    out = mcp_server.coach_doctrine("helms-training-pyramid/SKILL.md")
    body = out.split("\n\n", 1)[1]
    assert len(body) <= mcp_server._MAX_DOCTRINE_CHARS + 80  # + truncation notice


def test_unknown_and_escaping_topics_still_error():
    assert mcp_server.coach_doctrine("nope.md").startswith("error: unknown doctrine topic")
    assert mcp_server.coach_doctrine("../../AGENTS.md").startswith("error: unknown doctrine topic")
    assert mcp_server.coach_doctrine("helms-training-pyramid").startswith("error")
