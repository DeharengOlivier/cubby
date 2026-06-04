import json
from pathlib import Path

from cubby.app.report import SortOutcome, group_by_category, render_json, render_plan
from cubby.domain.file_ref import Stage


def _outcomes():
    return [
        SortOutcome(Path("/d/Invoice-1.pdf"), "Invoices", Stage.NAME),
        SortOutcome(Path("/d/x.png"), "Images", Stage.STRONG_EXT),
        SortOutcome(Path("/d/9f.pdf"), "Invoices", Stage.CONTENT),
    ]


def test_render_plan_groups_and_annotates():
    text = render_plan(_outcomes(), applied=False)
    assert "Invoices/  (2)" in text
    assert "Images/  (1)" in text
    assert "<- content" in text  # non-name stage annotated
    assert "Would move 3 item(s)." in text


def test_render_plan_empty():
    assert render_plan([], applied=True) == "Nothing to sort."


def test_group_by_category():
    grouped = group_by_category(_outcomes())
    assert {k: len(v) for k, v in grouped.items()} == {"Invoices": 2, "Images": 1}


def test_render_json_is_valid():
    payload = json.loads(render_json(_outcomes(), applied=False))
    assert payload["count"] == 3
    assert payload["applied"] is False
    assert payload["items"][0]["category"] == "Invoices"
    assert payload["items"][1]["stage"] == "strong-ext"
