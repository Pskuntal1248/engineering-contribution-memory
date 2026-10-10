"""Source adapters behind one interface. The graph only sees `fetch()`; where the data
comes from (real gateway tools, or fixtures in tests) is swappable.

A source that fails or does not exist is named in `failed`, never silently empty."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Protocol

from guards import ToolLog

Call = Callable[[str, dict], Awaitable[Any]]  # call("github.list_pr_reviews", {...}) -> parsed payload

MAX_PRS_PER_REPO = 600
MAX_COMMITS_PER_REPO = 300
MAX_COMMIT_LOOKUPS = 100
PARALLEL = 6  # simultaneous gateway calls
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
                 jira_projects: list[str], has: set[str], docs_sources: list[str] | None = None, names: list[str] | None = None):
        self.names = names or []
        self.docs_sources = [d.lower() for d in (docs_sources or [])]
        self.call, self.username, self.repos = call, username, repos
        self.slack_channels, self.include_dms, self.jira_projects, self.has = slack_channels, include_dms, jira_projects, has

    async def fetch(self, since: str, until: str | None, log: ToolLog) -> dict[str, Any]:
        out: dict[str, Any] = {"github": {"prs": [], "reviews": {}, "commits": []}, "jira": [], "slack": {"channels": [], "threads": []}, "docs": [], "failed": [], "capped": []}
        await self._github(out, since, log)
        if self.slack_channels or self.include_dms:  # no Slack call at all unless the user named channels
            await self._slack(out, since, log)
        import adapters
        if "jira.search_issues" in self.has:
            await adapters.fetch_jira(self.call, self.jira_projects, since, log, out)
        else:
            out["failed"].append({"source": "jira", "error": "jira.search_issues is not available in this run"})
        wanted = {"confluence": ("confluence.cql_search", adapters.fetch_confluence), "notion": ("notion.search_notion_page", adapters.fetch_notion),
                  "google drive": ("drive.find_file", adapters.fetch_drive)}
        for src_name, (tool, fn) in wanted.items():
            if not any(src_name in d or (src_name == "google drive" and "drive" in d) for d in self.docs_sources):
                continue
            if tool in self.has:
                await (fn(self.call, since, log, out, self.names) if src_name == "notion" else fn(self.call, since, log, out))
            else:
                out["failed"].append({"source": f"docs:{src_name}", "error": f"{tool} is not available in this run"})
        if not self.docs_sources:
            out["failed"].append({"source": "docs", "error": "no docs source chosen (set docs_source to Confluence, Notion or Google Drive)"})
        return out

    async def _github(self, out: dict, since: str, log: ToolLog) -> None:
        for repo in self.repos:
            try:
                await self._github_repo(out, repo, since, log)
            except Exception as e:  # noqa: BLE001
                out["failed"].append({"source": f"github:{repo}", "error": str(e)[:200]})
        if len(out["github"]["prs"]) >= MAX_PRS_PER_REPO * len(self.repos):
            out["capped"].append("github PRs hit the per-repo cap")

    async def _github_repo(self, out: dict, repo: str, since: str, log: ToolLog) -> None:
        owner, name = repo.split("/", 1)
        sem = asyncio.Semaphore(PARALLEL)  # bounded fan-out: same calls as before, just not one after another

        async def one(tool: str, args: dict) -> Any:
            async with sem:
                return await self.call(tool, args)

        log.add("github.search_issues", repo=repo)
        q = f"repo:{repo} is:pr author:{self.username} created:>={since[:10]}"
        prs = _rows(await self.call("github.search_issues", {"q": q, "per_page": 100}), "items")[:MAX_PRS_PER_REPO]
        log.add("github.search_issues", repo=repo, reviewed_by=self.username)
        rv_q = f"repo:{repo} is:pr reviewed-by:{self.username} -author:{self.username} created:>={since[:10]}"
        others = _rows(await self.call("github.search_issues", {"q": rv_q, "per_page": 100}), "items")[:300]
        # Search results often lack merged_at. The PR list carries it, so one call per repo fills it in by number.
        try:
            log.add("github.list_pull_requests", repo=repo, state="closed")
            listed = _rows(await self.call("github.list_pull_requests", {"owner": owner, "repo": name, "state": "closed", "per_page": 100}), "pull_requests")
            by_num = {x.get("number"): x for x in listed}
            for p in prs:
                full = by_num.get(p.get("number"))
                if full:
                    for k in ("merged_at", "closed_at", "merge_commit_sha", "merged", "head", "additions", "deletions", "labels"):
                        if full.get(k) not in (None, "", []) and not p.get(k):
                            p[k] = full[k]
        except Exception as e:  # noqa: BLE001 - merge state is then inferred from commits
            out["failed"].append({"source": f"github-pr-list:{repo}", "error": str(e)[:200]})

        async def files(p: dict, allowed: bool) -> None:
            if not allowed:
                return
            try:
                p["files"] = _rows(await one("github.list_pr_files", {"owner": owner, "repo": name, "pull_number": p.get("number")}))
            except Exception:  # noqa: BLE001 - totals are used instead, flagged via size basis
                p["files"] = []

        async def reviews(p: dict) -> None:
            key = f"{repo}#{p.get('number')}"
            try:
                out["github"]["reviews"][key] = _rows(await one("github.list_pr_reviews", {"owner": owner, "repo": name, "pull_number": p.get("number")}))
            except Exception as e:  # noqa: BLE001
                out["failed"].append({"source": f"github-reviews:{key}", "error": str(e)[:200]})

        jobs = []
        for n, p in enumerate(prs):
            p["_repo"] = repo
            jobs += [files(p, n < MAX_PR_FILE_LOOKUPS), reviews(p)]
            out["github"]["prs"].append(p)
        if len(prs) > MAX_PR_FILE_LOOKUPS:
            out["capped"].append(f"pr files for {repo}: first {MAX_PR_FILE_LOOKUPS} PRs only")
        for p in others:
            p["_repo"] = repo
            jobs.append(reviews(p))
            out["github"]["prs"].append(p)
        await asyncio.gather(*jobs)
        await self._commits(out, repo, since, log)

    async def _commits(self, out: dict, repo: str, since: str, log: ToolLog) -> None:
        """Commits by the user, so repos without PRs still show work. Bots, merges and PR-covered commits are dropped in ingest."""
        owner, name = repo.split("/", 1)
        try:
            log.add("github.list_commits", repo=repo)
            rows = _rows(await self.call("github.list_commits", {"owner": owner, "repo": name, "author": self.username, "since": since, "per_page": 100}), "commits")
        except Exception as e:  # noqa: BLE001 - named in the result
            out["failed"].append({"source": f"github-commits:{repo}", "error": str(e)[:200]})
            return
        if len(rows) >= MAX_COMMITS_PER_REPO:
            out["capped"].append(f"commits for {repo}: first {MAX_COMMITS_PER_REPO} only")
        sem = asyncio.Semaphore(PARALLEL)
        picked = rows[:MAX_COMMITS_PER_REPO]

        async def detail(n: int, c: dict) -> None:
            c["_repo"] = repo
            if n < MAX_COMMIT_LOOKUPS and not c.get("files"):  # list_commits has no file list; size needs one lookup per commit
                try:
                    async with sem:
                        full = await self.call("github.get_commit", {"owner": owner, "repo": name, "ref": c.get("sha", "")})
                    if isinstance(full, dict):
                        c["files"], c["stats"] = full.get("files"), full.get("stats")
                except Exception:  # noqa: BLE001 - size stays unknown, the commit is still listed
                    pass

        await asyncio.gather(*(detail(n, c) for n, c in enumerate(picked)))
        if len(picked) > MAX_COMMIT_LOOKUPS:
            out["capped"].append(f"commit sizes for {repo}: first {MAX_COMMIT_LOOKUPS} commits only")
        out["github"]["commits"].extend(picked)

    async def _list_channels(self, log: ToolLog) -> list[dict]:
        """All channels the connected account can see, page by page. slack.list_channels defaults to limit=1 and public channels only,
        so ask for public and private (and DMs only when the user opted in)."""
        import adapters
        types = "public_channel,private_channel" + (",im,mpim" if self.include_dms else "")
        chans: list[dict] = []
        cursor = ""
        for _ in range(30):
            args: dict[str, Any] = {"types": types, "limit": 1000, "exclude_archived": True}
            if cursor:
                args["cursor"] = cursor
            resp = await self.call("slack.list_channels", args)
            chans += _rows(resp, "channels")
            cursor = str(adapters.find_key(resp, "next_cursor") or "")
            if not cursor:
                break
        return chans

    async def _slack(self, out: dict, since: str, log: ToolLog) -> None:
        try:
            log.add("slack.list_channels")
            chans = await self._list_channels(log)
            out["slack"]["channels"] = chans
            from guards import channels_to_read, is_dm_channel
            picked, missing = channels_to_read(chans, self.slack_channels, self.include_dms, log)
            for m in missing:
                out["failed"].append({"source": f"slack:#{m}", "error": (
                    f"#{m} is not in the channel list of the connected Slack account ({len(chans)} channels listed, public and private). "
                    "Check: the name is spelled exactly; you are a member of it (a private channel is only listed for members); "
                    "and `gravity connect slack` was done in the same workspace. If the connection is older than the private channel, "
                    "reconnect it so private channels are readable.")})
            for ch in picked:
                if ch.get("is_member") is False:
                    out["failed"].append({"source": f"slack:#{ch.get('name', '')}", "error": "connected Slack account is not a member of this channel; join it, then rerun"})
            if "slack.search_messages" in self.has and "slack.fetch_message_thread_from_a_conversation" in self.has:
                import adapters
                await adapters.fetch_slack_search(self.call, [c for c in picked if not is_dm_channel(c)], since, log, out)
                picked = [c for c in picked if is_dm_channel(c)]  # opted-in DMs still read from history, channel by channel
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
