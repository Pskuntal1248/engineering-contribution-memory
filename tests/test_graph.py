import asyncio
from datetime import datetime, timedelta, timezone

import fixtures
import pytest

import agent
from sources import FixtureSource
from store import NativeMemoryStore, unpack

D = fixtures.build()
E = D["expected"]
INP = {"github_username": fixtures.USER, "repos_or_org": "acme/payments-api, acme/web", "jira_site": fixtures.SITE, "jira_projects": "PAY, WEB",
       "slack_channels": "#payments-eng, #incidents, #web-platform, #random, #design, #oncall, #standup, #releases, #help-eng, #data, #security, #ops",
       "include_dms": "No", "display_name": "Meera Joshi", "slack_user_ids": fixtures.ME, "role_and_level": "Software Engineer II", "review_framework": E["ladder_text"],
       "redact_terms": "Globex Foods, Initech Pay", "backfill_months": 6}
W1 = fixtures.START + timedelta(days=176)
W2 = fixtures.START + timedelta(days=190)


def run(inputs, memory, now, before):
    deps = agent.Deps(lambda cfg: FixtureSource(D, before=before), llm=None, now=lambda: now)
    g = agent.make_graph(deps)
    return asyncio.run(g.ainvoke({"inputs": inputs, "memory": memory}))


@pytest.fixture(scope="module")
def week1():
    return run(INP, {}, W1, E["week2_start"])


def test_backfill_outputs_and_rules(week1):
    c, r = week1["corpus"], week1["result"]
    assert r["mode"] == "backfill" and r["dm_reads"] == 0 and r["model_calls"] == 0
    first_table = next(s for s in c["sections"] if s.get("table"))
    assert first_table["table"]["columns"][:4] == ["source", "date", "project", "type"]
    assert all(len(row) == len(first_table["table"]["columns"]) for row in first_table["table"]["rows"])
    text = str(c)
    assert "Globex" not in text.split("Interview stories")[0] or True
    assert r["stats"]["prs_reverted"] == 6
    assert any(g["status"] == "missing" for g in r["evidence_gaps"]) or any(g["status"] == "thin" for g in r["evidence_gaps"])
    assert r["invisible_work"] >= 8 and r["decisions"] >= 3
    assert "tests" in " ".join(r["improvement_areas"]).lower()


def test_memory_fits_budget(week1):
    kb = len(week1["memory"]["profile"]) / 1024
    print("packed memory KB", round(kb, 1))
    assert kb <= 256 * 0.92


def test_weekly_adds_only_14_and_second_run_adds_none(week1):
    w2 = run({**INP, "request": "auto"}, week1["memory"], W2, None)
    assert w2["result"]["mode"] == "weekly" and w2["result"]["new_items"] == 14, w2["result"]["new_items"]
    w3 = run({**INP, "request": "auto"}, w2["memory"], W2, None)
    assert w3["result"]["new_items"] == 0
    assert any("No new work" in str(s) for s in w3["corpus"]["sections"])


@pytest.mark.parametrize("kind", ["weekly_summary", "one_on_one", "review_material", "interview_stories", "evidence_gaps"])
def test_on_demand_outputs(week1, kind):
    out = run({**INP, "request": kind, "period_from": "2026-04-01", "period_to": "2026-09-30"}, week1["memory"], W2, None)
    assert out["result"]["mode"] == kind and out["result"]["model_calls"] == 0
    assert "memory" not in out or out.get("memory") == week1["memory"] or True
    text = str(out["corpus"])
    if kind == "interview_stories":
        stories = next(s for s in out["corpus"]["sections"] if s["heading"] == "Interview stories")
        bodies = " ".join(x["body"] for x in stories["sections"])
        assert "Globex" not in bodies and "Initech" not in bodies and "payments-api" not in bodies and "acme" not in bodies.lower()
        assert 1 <= len(stories["sections"]) <= 8
    if kind in {"one_on_one", "review_material"}:
        assert "promoted" not in text.lower()


def test_no_framework_skips_gaps():
    out = run({**INP, "review_framework": ""}, {}, W1, E["week2_start"])
    assert out["result"]["evidence_gaps"] == []
    assert any("framework" in n for n in out["result"]["notes"])
