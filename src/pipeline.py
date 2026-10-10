"""Raw source bundle -> clean, deduped, filtered Items. Pure; the graph and the tests both call this."""
from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Any

from guards import ToolLog, channels_to_read, drop_unconsented, parse_channels
from models import Item, Skipped
import normalize as N


@dataclass
class Config:
    username: str
    repos: list[str]
    jira_site: str = ""
    jira_projects: list[str] = field(default_factory=list)
    slack_channels: list[str] = field(default_factory=list)
    include_dms: bool = False
    slack_user_ids: set[str] = field(default_factory=set)
    name_hints: list[str] = field(default_factory=list)  # display names / emails for Jira + docs
    repo_aliases: dict[str, str] = field(default_factory=dict)
    docs_sources: list[str] = field(default_factory=list)


@dataclass
class Ingest:
    items: list[Item]
    skipped: list[Skipped]
    duplicates: int
    dm_dropped: int
    not_found_channels: list[str]
    counts: dict[str, int]


def infer_merged(prs: list[dict], commits: list[dict]) -> None:
    """Closed PRs with no merge time from the gateway: confirm the merge from the repo's own commits
    (merge_commit_sha seen, 'Merge pull request #N', or 'title (#N)'). Unconfirmed ones stay unmerged."""
    for p in prs:
        if N.pr_merged_at(p) or str(p.get("state", "")).lower() != "closed":
            continue
        repo, num = p.get("_repo", ""), p.get("number")
        sha = p.get("merge_commit_sha") or (p.get("pull_request") or {}).get("merge_commit_sha") or ""
        for c in commits:
            if c.get("_repo") != repo:
                continue
            msg = ((c.get("commit") or {}).get("message") or c.get("message") or "").splitlines()
            first = msg[0] if msg else ""
            hit = bool(sha and c.get("sha") == sha) or re.match(rf"Merge pull request #{num}\b", first) or re.search(rf"\(#{num}\)\s*$", first)
            if hit:
                when = (c.get("commit") or {}).get("committer", {}).get("date") or (c.get("commit") or {}).get("author", {}).get("date") or p.get("closed_at")
                p["merged_at"], p["_merged_basis"] = when, "merge commit found in repo history"
                break


def covered_by_pr(c: Item, prs: list[dict]) -> bool:
    """A commit that is part of one of the user's PRs: same title, or only files the PR touched, made inside the PR's life."""
    cd = N.parse_ts(c.date)
    cpaths = set(c.meta.get("paths") or [])
    for p in prs:
        if p.get("_repo") != c.meta.get("repo") or N._login(p.get("user")).lower() != str(c.author).lower():
            continue
        if (p.get("title") or "").strip() and (p.get("title") or "").strip() == c.title.strip():
            return True
        start, end = N.parse_ts(p.get("created_at")), N.parse_ts(N.pr_merged_at(p) or p.get("closed_at"))
        ppaths = {f.get("filename") for f in (p.get("files") or []) if isinstance(f, dict)}
        if cd and start and end and cpaths and ppaths and cpaths <= ppaths and start.timestamp() - 7 * 86400 <= cd.timestamp() <= end.timestamp() + 120:
            return True
    return False


def ingest(raw: dict[str, Any], cfg: Config, log: ToolLog | None = None) -> Ingest:
    items: list[Item] = []
    skipped: list[Skipped] = []

    def take(x: Item | Skipped) -> None:
        (items if isinstance(x, Item) else skipped).append(x)

    prs = raw.get("github", {}).get("prs", [])
    reviews = raw.get("github", {}).get("reviews", {})
    pr_by_key: dict[str, dict] = {}
    infer_merged(prs, raw.get("github", {}).get("commits", []))
    for p in prs:
        repo = cfg.repo_aliases.get(p.get("_repo", ""), p.get("_repo", ""))
        pr_by_key[f"{p.get('_repo','')}#{p.get('number')}"] = p
        authored_by_me = N._login(p.get("user")).lower() == cfg.username.lower()
        if authored_by_me:
            take(N.normalize_pr(p, repo, cfg.username, cfg.jira_projects))
    for key, revs in reviews.items():
        repo, num = key.rsplit("#", 1)
        pr = pr_by_key.get(key, {})
        for rv in revs:
            reviewer = N._login(rv.get("user"))
            pr_author = N._login(pr.get("user"))
            # keep reviews I wrote, and reviews I received on my PRs (feedback patterns); drop everyone else's
            if reviewer.lower() == cfg.username.lower() and pr_author.lower() == cfg.username.lower():
                skipped.append(Skipped(f"gh:review:{repo}#{num}:{rv.get('id')}", "own comment on own PR (not a review given)"))
                continue
            if reviewer.lower() == cfg.username.lower() or pr_author.lower() == cfg.username.lower():
                take(N.normalize_review(rv, cfg.repo_aliases.get(repo, repo), int(num), pr_author, pr.get("html_url", ""), cfg.username, cfg.jira_projects))
    pr_nums = {(p.get("_repo", ""), str(p.get("number"))) for p in prs}
    for c in raw.get("github", {}).get("commits", []):
        x = N.normalize_commit(c, c.get("_repo", ""), cfg.username, cfg.jira_projects)
        if isinstance(x, Item):
            first = x.title
            m = re.search(r"\(#(\d+)\)\s*$", first) or re.match(r"Merge pull request #(\d+)", first)
            if not x.meta.get("mine"):
                x = Skipped(x.id, "commit by someone else")
            elif first.startswith("Merge ") or (m and (c.get("_repo", ""), m.group(1)) in pr_nums):
                x = Skipped(x.id, "merge commit or commit already counted through its PR")
            elif covered_by_pr(x, prs):
                x = Skipped(x.id, "commit already counted through its PR (same title or same files, inside the PR's dates)")
        take(x)
    for t in raw.get("jira", []):
        take(N.normalize_ticket(t, cfg.jira_site, cfg.name_hints))
    channels = raw.get("slack", {}).get("channels", [])
    picked, missing = channels_to_read(channels, cfg.slack_channels, cfg.include_dms, log)
    allowed = {c["id"] for c in picked}
    names = {c["id"]: c["name"] for c in channels}
    dm_ids = {c["id"] for c in channels if c.get("is_im") or c.get("is_mpim")}
    for th in raw.get("slack", {}).get("threads", []):
        if th["channel_id"] not in allowed:
            skipped.append(Skipped(f"slack:{th['channel_id']}", "channel not chosen"))
            continue
        if log is not None:
            log.add("slack.read_history", channel=th["channel_id"], is_dm=th["channel_id"] in dm_ids)
        x = N.normalize_thread(names[th["channel_id"]], th["channel_id"], th["messages"], cfg.slack_user_ids | set(raw.get("slack", {}).get("my_user_ids", [])))
        if isinstance(x, Item):
            x.meta["is_dm"] = th["channel_id"] in dm_ids
        take(x)
    for d in raw.get("docs", []):
        take(N.normalize_doc(d, d.get("_source", "confluence"), cfg.name_hints))

    items = [i for i in items if i.date]
    items, dm_dropped = drop_unconsented(items, cfg.include_dms)
    items, dups = N.dedupe(items)
    N.mark_reverted([i for i in items if i.type == "pr"])
    N.mark_reverted_commits([i for i in items if i.type == "commit"])
    counts: dict[str, int] = {}
    for i in items:
        counts[i.type] = counts.get(i.type, 0) + 1
    return Ingest(items, skipped, dups, dm_dropped, missing, counts)
