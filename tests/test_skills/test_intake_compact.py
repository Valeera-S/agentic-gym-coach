"""coach_intake_status default output is compact; detail=true is the full report."""

from __future__ import annotations

import json

import pytest

from coach_tools import DISPATCH, error_payload

COMPACT_KEYS = {"name", "status", "value", "blocks_now", "round", "gates"}


def test_default_fields_are_compact():
    out = DISPATCH["intake_status"]({})
    assert out["fields"]
    for f in out["fields"]:
        assert set(f) == COMPACT_KEYS


def test_default_output_is_small_on_an_empty_profile():
    size = len(json.dumps(DISPATCH["intake_status"]({})))
    assert size < 8000, size


def test_next_round_carries_full_definitions_for_its_fields():
    out = DISPATCH["intake_status"]({})
    nr = out["next_round"]
    assert nr["round"] == 1 and nr["fields"]
    assert [d["name"] for d in nr["field_details"]] == nr["fields"]
    for d in nr["field_details"]:
        assert d["question"] and d["source"]
        assert "note" in d
    assert any(d["options"] for d in nr["field_details"])
    opt = next(o for d in nr["field_details"] for o in d["options"])
    assert {"value", "means", "effect", "source"} <= set(opt)


def test_detail_true_is_the_full_report():
    from skills.intake import assess_intake
    out = DISPATCH["intake_status"]({"detail": True})
    assert out == assess_intake().model_dump(mode="json")
    assert "question" in out["fields"][0] and "options" in out["fields"][0]
    assert "field_details" not in out["next_round"]


def test_other_keys_unchanged_between_modes():
    a, b = DISPATCH["intake_status"]({}), DISPATCH["intake_status"]({"detail": True})
    for k in ("training_ready", "nutrition_ready", "missing", "missing_by_gate", "progress",
              "rounds_total", "weeks_since_last_session"):
        assert a[k] == b[k]


@pytest.mark.parametrize("bad", ["yes", 1, None, "true"])
def test_detail_must_be_a_strict_bool(bad):
    with pytest.raises(ValueError) as e:
        DISPATCH["intake_status"]({"detail": bad})
    assert error_payload(e.value)["error"] == "invalid_input"


def test_mcp_wrapper_declares_detail():
    import asyncio
    import mcp_server
    tools = {t.name: t.input_schema for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert "detail" in tools["coach_intake_status"]["properties"]
    res = asyncio.run(mcp_server.mcp.call_tool("coach_intake_status", {"detail": True}))
    assert "question" in json.loads(res.content[0].text)["fields"][0]
