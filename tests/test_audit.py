"""Spec audit: typed requests, team changes, include_dms opt-in semantics."""
from datetime import date

from ask import parse_request, parse_team_changes
from guards import ToolLog, channels_to_read
from linking import split_by_team
from models import Item

CH = [{"id": "C1", "name": "general"}, {"id": "G1", "name": "secret", "is_private": True},
      {"id": "D1", "name": "", "user": "U77", "is_im": True}, {"id": "D2", "name": "", "user": "U88", "is_im": True}]
TODAY = date(2026, 10, 10)


def test_typed_requests_and_date_ranges():
    assert parse_request("weekly summary", TODAY)[0] == "weekly_summary"
    assert parse_request("1:1 points", TODAY)[0] == "one_on_one"
    assert parse_request("interview stories", TODAY)[0] == "interview_stories"
    assert parse_request("evidence gaps", TODAY)[0] == "evidence_gaps"
    assert parse_request("review material for April to September", TODAY) == ("review_material", "2026-04-01", "2026-09-30")
    assert parse_request("review material for November to March", TODAY) == ("review_material", "2025-11-01", "2026-03-31")
    assert parse_request("review material 2026-01-01 to 2026-03-31", TODAY) == ("review_material", "2026-01-01", "2026-03-31")
    assert parse_request("", TODAY) == ("", "", "")


def test_team_change_groups_by_team_with_date():
    ch = parse_team_changes("2026-07-01: Platform\n2026-03-01: Payments\nnot a line")
    assert ch == [("2026-03-01", "Payments"), ("2026-07-01", "Platform")]
    mk = lambda i, d: Item(id=i, source="github", type="pr", date=d, title="t", text="", author="me", meta={})
    out = split_by_team([mk("a", "2026-04-01T00:00:00Z"), mk("b", "2026-08-01T00:00:00Z")], ch)
    assert out == {"Payments": ["a"], "Platform": ["b"]}


def test_include_dms_no_never_reads_dms_even_when_named():
    log = ToolLog()
    picked, _ = channels_to_read(CH, ["general", "U77", "*dm*"], False, log)
    assert [c["id"] for c in picked] == ["C1"] and not log.dm_reads()


def test_include_dms_yes_reads_only_dms_the_user_names():
    picked, missing = channels_to_read(CH, ["general", "U77"], True)
    assert [c["id"] for c in picked] == ["C1", "D1"] and missing == []
    picked, _ = channels_to_read(CH, ["general"], True)
    assert [c["id"] for c in picked] == ["C1"]  # Yes alone opts in nothing
    picked, _ = channels_to_read(CH, ["general", "*dm*"], True)
    assert {c["id"] for c in picked} == {"C1", "D1", "D2"}


def test_private_channel_only_when_listed():
    assert [c["id"] for c in channels_to_read(CH, ["general"], False)[0]] == ["C1"]
    assert "G1" in [c["id"] for c in channels_to_read(CH, ["general", "secret"], False)[0]]


def test_markdown_copy_has_text_and_ledger(tmp_path):
    import outputs
    corpus = {"title": "T", "summary": "S", "sections": [{"heading": "Wins", "bullets": ["shipped X (PAY-1)"]},
                                                           {"heading": "Item ledger", "table": {"columns": ["source", "date"], "rows": [["github", "2026-09-01"]]}}]}
    md = outputs.to_markdown(corpus, with_tables=False)
    assert "shipped X (PAY-1)" in md and "github" not in md
    outputs.write_local_copy(corpus, str(tmp_path))
    assert "shipped X" in (tmp_path / "last_output.md").read_text() and (tmp_path / "last_ledger.csv").read_text().startswith("source,date")
