"""PR-commit SHA membership as hard dedup evidence, and pre-read validation of Slack channel and Jira project inputs."""
import asyncio
from datetime import datetime, timezone

from guards import ToolLog, channels_to_read, parse_channels
from pipeline import Config, ingest
from sources import GatewaySource
from test_adapters import Gateway, HAS, ME, SINCE
from test_fixes3 import CFG, commit_dict, pr_dict, raw


# ---------------------------------------------------------- SHA membership dedup

def test_pr_commit_sha_membership_folds_shadow_commit():
    # different title, different files, no shared key: the SHA being one of the PR's own commits is the definitive relation
    p = pr_dict(1, "Add result ranking", files=("src/search.py",))
    p["_commit_shas"] = ["d" * 40]
    out = ingest(raw([p], [commit_dict("d" * 40, "SRCH-202 Add result ranking v2", files=("c_search.go",))]), CFG)
    assert [i.type for i in out.items] == ["pr"]
    assert any("counted through its PR" in s.reason for s in out.skipped)


def test_commit_sha_not_in_pr_stays_separate():
    p = pr_dict(1, "Add result ranking", files=("src/search.py",))
    p["_commit_shas"] = ["e" * 40]
    out = ingest(raw([p], [commit_dict("f" * 40, "SRCH-202 Add result ranking v2", files=("c_search.go",))]), CFG)
    assert sorted(i.type for i in out.items) == ["commit", "pr"]


# ---------------------------------------------------------- Slack input validation

def test_channel_matched_by_id_as_well_as_name():
    chans = [{"id": "C0123", "name": "general"}, {"id": "C9999", "name": "payments-eng"}]
    picked, missing = channels_to_read(chans, ["C0123"], False)
    assert [c["name"] for c in picked] == ["general"] and missing == []
    picked, missing = channels_to_read(chans, ["payments-eng", "genral"], False)
    assert [c["name"] for c in picked] == ["payments-eng"] and missing == ["genral"]  # a typo is reported, never substituted


def test_unknown_channel_lists_visible_channels_and_suggests():
    gw = Gateway()
    out = asyncio.run(GatewaySource(gw, ME, [], parse_channels("payments-engr"), False, [], HAS).fetch(SINCE, None, ToolLog()))
    err = next(f["error"] for f in out["failed"] if f["source"] == "slack:#payments-engr")
    assert "nothing was read" in err
    assert "Did you mean: #payments-eng?" in err
    assert "#random" in err  # the visible list is shown so the user can choose
    assert out["slack"]["threads"] == []


def test_channel_id_accepted_end_to_end():
    gw = Gateway()
    out = asyncio.run(GatewaySource(gw, ME, [], parse_channels("C1"), False, [], HAS).fetch(SINCE, None, ToolLog()))
    assert [t["channel_id"] for t in out["slack"]["threads"]] == ["C1"]


# ---------------------------------------------------------- Jira project preflight

class JiraProjects(Gateway):
    async def __call__(self, name, args):
        if name == "jira.get_all_projects":
            return {"values": [{"key": "PAY"}, {"key": "SRCH"}]}
        return await super().__call__(name, args)


def run_jira(gw, projects, has):
    return asyncio.run(GatewaySource(gw, ME, [], parse_channels("#payments-eng"), False, projects, has, [], ["Parth Singh"]).fetch(SINCE, None, ToolLog()))


def test_jira_preflight_canonicalises_case_and_reports_typo():
    gw = JiraProjects()
    out = run_jira(gw, ["pay", "PAY2"], HAS | {"jira.get_all_projects"})
    q = next(a for n, a in gw.calls if n == "jira.search_issues")
    assert "project in (PAY)" in q["jql"]  # the lowercase input resolves to the real key
    err = next(f["error"] for f in out["failed"] if f["source"] == "jira:PAY2")
    assert "not visible" in err and "Did you mean: PAY?" in err and "SRCH" in err
    assert [t["key"] for t in out["jira"]] == ["PAY-1", "PAY-2", "PAY-3"]  # the valid project is still read


def test_jira_preflight_all_keys_wrong_reads_nothing():
    gw = JiraProjects()
    out = run_jira(gw, ["NOPE"], HAS | {"jira.get_all_projects"})
    assert out["jira"] == []
    assert not any(n == "jira.search_issues" for n, _ in gw.calls)
    assert any(f["source"] == "jira:NOPE" for f in out["failed"])


class JiraProjectsRefused(Gateway):
    async def __call__(self, name, args):
        if name == "jira.get_all_projects":
            raise RuntimeError("401 Unauthorized")
        return await super().__call__(name, args)


def test_jira_preflight_refused_uses_keys_as_given_and_says_so():
    gw = JiraProjectsRefused()
    out = run_jira(gw, ["PAY"], HAS | {"jira.get_all_projects"})
    assert [t["key"] for t in out["jira"]] == ["PAY-1", "PAY-2", "PAY-3"]
    assert any("could not be checked" in f["error"] for f in out["failed"] if f["source"] == "jira-preflight")
