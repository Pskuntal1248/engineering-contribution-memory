"""Raw source bundle -> clean, deduped, filtered Items. Pure; the graph and the tests both call this."""
from __future__ import annotations

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


@dataclass
class Ingest:
    items: list[Item]
    skipped: list[Skipped]
    duplicates: int
    dm_dropped: int
    not_found_channels: list[str]
    counts: dict[str, int]


def ingest(raw: dict[str, Any], cfg: Config, log: ToolLog | None = None) -> Ingest:
    items: list[Item] = []
    skipped: list[Skipped] = []

    def take(x: Item | Skipped) -> None:
        (items if isinstance(x, Item) else skipped).append(x)

    prs = raw.get("github", {}).get("prs", [])
    reviews = raw.get("github", {}).get("reviews", {})
    pr_by_key: dict[str, dict] = {}
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
            if reviewer.lower() == cfg.username.lower() or pr_author.lower() == cfg.username.lower():
                take(N.normalize_review(rv, cfg.repo_aliases.get(repo, repo), int(num), pr_author, pr.get("html_url", ""), cfg.username, cfg.jira_projects))
    for c in raw.get("github", {}).get("commits", []):
        take(N.normalize_commit(c, c.get("_repo", ""), cfg.username, cfg.jira_projects))
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
        x = N.normalize_thread(names[th["channel_id"]], th["channel_id"], th["messages"], cfg.slack_user_ids)
        if isinstance(x, Item):
            x.meta["is_dm"] = th["channel_id"] in dm_ids
        take(x)
    for d in raw.get("docs", []):
        take(N.normalize_doc(d, d.get("_source", "confluence"), cfg.name_hints))

    items = [i for i in items if i.date]
    items, dm_dropped = drop_unconsented(items, cfg.include_dms)
    items, dups = N.dedupe(items)
    N.mark_reverted([i for i in items if i.type == "pr"])
    counts: dict[str, int] = {}
    for i in items:
        counts[i.type] = counts.get(i.type, 0) + 1
    return Ingest(items, skipped, dups, dm_dropped, missing, counts)
