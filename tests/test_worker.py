"""Worker: what it sends back is ids, verified-format codes, enums and clipped read fields only."""

import pytest

from worker.worker import config, to_wire


def scan(verdict, matches, read=None):
    return {"verdict": verdict, "matches": matches, "read": read or {}}


def test_possible_match_without_label_asks_for_one():
    item = scan("POSSIBLE MATCH", [{"recall_id": 7, "exact": ["brand"]}], {"category": "stroller"})
    w = to_wire(item, item, None, 3.2)
    assert w["notes"] == ["label_ask"] and w["shortlist"] == [{"recall_id": 7, "codes": []}]


def test_label_confirmation_keeps_only_well_formed_codes():
    item = scan("POSSIBLE MATCH", [{"recall_id": 7, "exact": []}])
    label = scan("CONFIRMED", [{"recall_id": 7, "exact": ["model:AB1234", "model:<x>", "brand"]}],
                 {"model_number": "AB1234"})
    w = to_wire(item, label, label, 5.0)
    assert w["verdict"] == "CONFIRMED" and w["shortlist"][0]["codes"] == ["model:AB1234"] and w["notes"] == []


def test_label_without_code_and_other_recall_notes():
    item = scan("POSSIBLE MATCH", [{"recall_id": 7, "exact": []}])
    label = scan("NO MATCH FOUND", [], {})
    assert to_wire(item, item | {"verdict": "POSSIBLE MATCH"}, label, 1)["notes"] == ["label_no_code"]
    other = scan("CONFIRMED", [{"recall_id": 9, "exact": ["upc:012345678905"]}])
    assert "label_other_recall" in to_wire(item, other, other, 1)["notes"]


def test_read_fields_are_clipped_and_control_characters_removed():
    item = scan("NO MATCH FOUND", [], {"category": "toy\x00\x1b" + "x" * 500, "brand": None})
    w = to_wire(item, item, None, 1)
    assert len(w["read"]["category"]) == 80 and "\x00" not in w["read"]["category"] and w["read"]["brand"] == ""
    assert w["shortlist"] == []


@pytest.mark.parametrize("url", ["http://example.com", "https://example.com/path", "ftp://x", ""])
def test_worker_refuses_non_https_or_pathful_server_urls(url, monkeypatch):
    monkeypatch.setenv("CTL_SERVER_URL", url)
    monkeypatch.setenv("CTL_WORKER_SECRET", "s" * 40)
    with pytest.raises(SystemExit):
        config()


def test_worker_needs_a_strong_secret(monkeypatch):
    monkeypatch.setenv("CTL_SERVER_URL", "https://example.onrender.com")
    monkeypatch.setenv("CTL_WORKER_SECRET", "short")
    with pytest.raises(SystemExit):
        config()


@pytest.mark.parametrize("raw,want", [
    ("!-shirt, '') }}<tool_call>", ""), ("none_found_or_unreadable_in_image", ""), ("N/A", ""),
    ("Graco Pack 'n Play", "Graco Pack 'n Play"), ("WK8283CU", "WK8283CU"), ("baby\tsnowsuit\n", "baby snowsuit"),
])
def test_display_field_drops_markup_and_placeholders(raw, want):
    from worker.worker import display_field

    assert display_field(raw, 80) == want


def test_closest_recalls_are_sent_only_with_no_match():
    item = scan("NO MATCH FOUND", [{"recall_id": 7, "exact": ["model:AB1234"]}]) | {"closest": True}
    w = to_wire(item, item, None, 1)
    assert w["closest"] and w["shortlist"] == [{"recall_id": 7, "codes": ["model:AB1234"]}]
    item = scan("NO MATCH FOUND", [{"recall_id": 7, "exact": []}])
    assert to_wire(item, item, None, 1)["shortlist"] == []
