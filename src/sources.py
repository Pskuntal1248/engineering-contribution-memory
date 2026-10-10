"""Source adapters behind one interface. The graph only sees `fetch()`; where the data
comes from (real gateway tools, or fixtures in tests) is swappable.

A source that fails or does not exist is named in `failed`, never silently empty."""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Protocol

from guards import ToolLog

Call = Callable[[str, dict], Awaitable[Any]]  # call("github.list_pr_reviews", {...}) -> parsed payload

MAX_PRS_PER_REPO = 600
MAX_PR_FILE_LOOKUPS = 400  # file lists are one call per PR; bounded and reported


class Source(Protocol):
    async def fetch(self, since: str, until: str | None, log: ToolLog) -> dict[str, Any]: ...


def _rows(data: Any, *keys: str) -> list[dict]:
    """Gateway payloads are lists, or dicts that hold the list under one of a few names."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in (*keys, "items", "data", "results", "values"):
            v = data.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                got = _rows(v, *keys)
                if got:
                    return got
    return []


class FixtureSource:
    """Serves a prebuilt raw bundle (tests, local demo), cut off at `before` (ISO) and `since` like a real source."""

    def __init__(self, raw: dict[str, Any], before: str | None = None):
        self.raw, self.before = raw, before

    async def fetch(self, since: str, until: str | None, log: ToolLog) -> dict[str, Any]:
        from datetime import datetime, timezone
        hi = self.before or "9999"
        lo = since

        def ok(ts: str) -> bool:
            return lo <= ts < hi

        def tsiso(x: str) -> str:
            return datetime.fromtimestamp(float(x), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        g = self.raw["github"]
        prs = [p for p in g["prs"] if ok(p["created_at"])]
        keys = {f"{p['_repo']}#{p['number']}" for p in prs}
        return {
            "github": {"prs": prs, "reviews": {k: v for k, v in g["reviews"].items() if k in keys}, "commits": []},
            "jira": [t for t in self.raw["jira"] if ok(t["fields"]["created"])],
            "slack": {"channels": self.raw["slack"]["channels"], "threads": [t for t in self.raw["slack"]["threads"] if ok(tsiso(t["messages"][0]["ts"]))]},
            "docs": [d for d in self.raw["docs"] if ok(d["modified"])], "failed": [], "capped": [],
        }


class GatewaySource:
    """Real tools through the Gravity gateway. Only certified read tools are used by default.
    NOTE: argument names are best guesses from the tool descriptions; they are unverified until a run
    against a connected account (see report). Every failure is captured per source."""

    def __init__(self, call: Call, username: str, repos: list[str], slack_channels: list[str], include_dms: bool,
                 jira_projects: list[str], has: set[str]):
        self.call, self.username, self.repos = call, username, repos
        self.slack_channels, self.include_dms, self.jira_projects, self.has = slack_channels, include_dms, jira_projects, has

    async def fetch(self, since: str, until: str | None, log: ToolLog) -> dict[str, Any]:
        out: dict[str, Any] = {"github": {"prs": [], "reviews": {}, "commits": []}, "jira": [], "slack": {"channels": [], "threads": []}, "docs": [], "failed": [], "capped": []}
        await self._github(out, since, log)
        await self._slack(out, since, log)
        if "jira.search" in self.has:
            try:
                log.add("jira.search", projects=self.jira_projects)
                data = await self.call("jira.search", {"jql": f"project in ({','.join(self.jira_projects)}) AND updated >= \"{since[:10]}\" AND (assignee = currentUser() OR reporter = currentUser() OR commentedBy = currentUser())"})
                out["jira"] = _rows(data, "issues")
            except Exception as e:  # noqa: BLE001 - named in the result
                out["failed"].append({"source": "jira", "error": str(e)[:200]})
        else:
            out["failed"].append({"source": "jira", "error": "no Jira tool in the Gravity catalog (requested from Gravity)"})
        out["failed"].append({"source": "docs", "error": "no docs tool available (Confluence/Notion are not in the Gravity catalog yet)"})
        return out

    async def _github(self, out: dict, since: str, log: ToolLog) -> None:
        looked = 0
        for repo in self.repos:
            try:
                log.add("github.search_issues", repo=repo)
                q = f"repo:{repo} is:pr author:{self.username} created:>={since[:10]}"
                prs = _rows(await self.call("github.search_issues", {"q": q, "per_page": 100}), "items")
                for p in prs[:MAX_PRS_PER_REPO]:
                    num = p.get("number")
                    p["_repo"] = repo
                    owner, name = repo.split("/", 1)
                    if looked < MAX_PR_FILE_LOOKUPS:
                        looked += 1
                        try:
                            p["files"] = _rows(await self.call("github.list_pr_files", {"owner": owner, "repo": name, "pull_number": num}))
                        except Exception:  # noqa: BLE001 - totals are used instead, flagged via size basis
                            p["files"] = []
                    else:
                        out["capped"].append(f"pr files for {repo}: first {MAX_PR_FILE_LOOKUPS} PRs only")
                    out["github"]["prs"].append(p)
                    out["github"]["reviews"][f"{repo}#{num}"] = _rows(await self.call("github.list_pr_reviews", {"owner": owner, "repo": name, "pull_number": num}))
                # PRs by others that the user reviewed
                log.add("github.search_issues", repo=repo, reviewed_by=self.username)
                rv = _rows(await self.call("github.search_issues", {"q": f"repo:{repo} is:pr reviewed-by:{self.username} -author:{self.username} created:>={since[:10]}", "per_page": 100}), "items")
                for p in rv[:300]:
                    p["_repo"] = repo
                    out["github"]["prs"].append(p)
                    owner, name = repo.split("/", 1)
                    out["github"]["reviews"][f"{repo}#{p.get('number')}"] = _rows(await self.call("github.list_pr_reviews", {"owner": owner, "repo": name, "pull_number": p.get("number")}))
            except Exception as e:  # noqa: BLE001
                out["failed"].append({"source": f"github:{repo}", "error": str(e)[:200]})
        if len(out["github"]["prs"]) >= MAX_PRS_PER_REPO * len(self.repos):
            out["capped"].append("github PRs hit the per-repo cap")

    async def _slack(self, out: dict, since: str, log: ToolLog) -> None:
        try:
            log.add("slack.list_channels")
            chans = _rows(await self.call("slack.list_channels", {}), "channels")
            out["slack"]["channels"] = chans
            from guards import channels_to_read, is_dm_channel
            picked, missing = channels_to_read(chans, self.slack_channels, self.include_dms, log)
            for m in missing:
                out["failed"].append({"source": f"slack:#{m}", "error": "channel not found or not accessible"})
            for ch in picked:
                log.add("slack.read_history", channel=ch["id"], is_dm=is_dm_channel(ch))
                msgs = _rows(await self.call("slack.read_history", {"channel": ch["id"], "oldest": since, "limit": 1000}), "messages")
                groups: dict[str, list[dict]] = {}
                for m in msgs:
                    groups.setdefault(str(m.get("thread_ts") or m.get("ts")), []).append(m)
                for ts, ms in groups.items():
                    out["slack"]["threads"].append({"channel": ch.get("name", ""), "channel_id": ch["id"], "messages": ms})
        except Exception as e:  # noqa: BLE001
            out["failed"].append({"source": "slack", "error": str(e)[:200]})
