"""Read-only adapters for Jira, Confluence, Notion, Google Drive and Slack search, on Gravity's gateway tools.
Each returns plain rows in the shape normalize.py expects, and names any failure instead of hiding it.
Argument names come from the gateway's own tool catalog (gravity tools); response shapes are parsed defensively
and are NOT verified against a connected account until a real run."""
from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable

from guards import ToolLog

Call = Callable[[str, dict], Awaitable[Any]]
PARALLEL = 6
MAX_TICKETS = 600           # 3x the spec's 200
MAX_TICKET_LOOKUPS = 600
MAX_DOCS_PER_SOURCE = 120   # 3x the spec's 40
MAX_SLACK_HITS = 1500
MAX_THREAD_FETCHES = 600

JIRA_FIELDS = ["summary", "status", "assignee", "reporter", "created", "updated", "resolutiondate", "issuetype", "parent", "labels", "description", "project", "comment"]


def rows(data: Any, *keys: str) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in (*keys, "items", "data", "results", "values"):
            v = data.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                got = rows(v, *keys)
                if got:
                    return got
    return []


def adf_text(node: Any) -> str:
    """Jira descriptions and comments may be Atlassian Document Format. Plain text out, no markup."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return " ".join(t for t in (adf_text(n) for n in node) if t)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return str(node.get("text", ""))
        parts = [adf_text(node.get("content"))] if node.get("content") else []
        if node.get("type") == "inlineCard":
            parts.append(str((node.get("attrs") or {}).get("url", "")))
        return "\n".join(p for p in parts if p)
    return ""


def text_of(data: Any, depth: int = 0) -> str:
    """Body text from whatever shape a docs tool returns, with HTML tags removed."""
    if data is None or depth > 6:
        return ""
    if isinstance(data, str):
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", data)).strip() if "<" in data else data
    if isinstance(data, list):
        return " ".join(t for t in (text_of(x, depth + 1) for x in data) if t)
    if isinstance(data, dict):
        for k in ("markdown", "content", "text", "value", "body", "storage", "view", "data"):
            if k in data:
                got = text_of(data[k], depth + 1)
                if got:
                    return got
        return " ".join(t for t in (text_of(v, depth + 1) for k, v in data.items() if k not in {"id", "url", "_links"}) if t)
    return ""


async def _gather(sem: asyncio.Semaphore, call: Call, name: str, args: dict) -> Any:
    async with sem:
        return await call(name, args)


# ------------------------------------------------------------------ Jira
async def fetch_jira(call: Call, projects: list[str], since: str, log: ToolLog, out: dict) -> None:
    if not projects:
        out["failed"].append({"source": "jira", "error": "no Jira project keys given"})
        return
    jql = (f'project in ({",".join(projects)}) AND updated >= "{since[:10]}" '
           f'AND (assignee = currentUser() OR reporter = currentUser() OR commentedBy = currentUser()) ORDER BY updated DESC')
    found: list[dict] = []
    token: str | None = None
    try:
        for _ in range(12):
            args: dict[str, Any] = {"jql": jql, "max_results": 100, "fields": JIRA_FIELDS}
            if token:
                args["next_page_token"] = token
            log.add("jira.search_issues", projects=projects)
            data = await call("jira.search_issues", args)
            found += rows(data, "issues")
            token = (data.get("next_page_token") or data.get("nextPageToken")) if isinstance(data, dict) else None
            if not token or len(found) >= MAX_TICKETS or (isinstance(data, dict) and data.get("isLast") is True):
                break
    except Exception as e:  # noqa: BLE001
        out["failed"].append({"source": "jira", "error": str(e)[:200]})
        if not found:
            return
    if len(found) >= MAX_TICKETS:
        out["capped"].append(f"jira tickets: first {MAX_TICKETS} only")
    sem = asyncio.Semaphore(PARALLEL)

    async def detail(n: int, t: dict) -> None:
        t["_mine"] = True  # the query only returns tickets assigned to, created by or commented on by the connected user
        key = t.get("key")
        if not key or n >= MAX_TICKET_LOOKUPS:
            return
        try:
            log.add("jira.get_issue", key=key)
            full = await _gather(sem, call, "jira.get_issue", {"issue_id_or_key": key, "expand": "changelog", "fields": JIRA_FIELDS})
            if isinstance(full, dict):
                full = full.get("issue", full)
                if isinstance(full, dict):
                    if full.get("fields"):
                        t["fields"] = {**(t.get("fields") or {}), **full["fields"]}
                    if full.get("changelog"):
                        t["changelog"] = full["changelog"]
                    if full.get("key") and full["key"] != key:
                        t["key"] = full["key"]
                        t.setdefault("_aliases", []).append(key)
        except Exception as e:  # noqa: BLE001 - the ticket is still listed, without history
            out["failed"].append({"source": f"jira-history:{key}", "error": str(e)[:120]})
            return
        c = (t.get("fields") or {}).get("comment") or {}
        have = c.get("comments") or []
        total = c.get("total", len(have))
        if total > len(have):  # the issue carries only some comments: page the rest
            try:
                got: list[dict] = list(have)
                while len(got) < min(total, 300):
                    log.add("jira.list_issue_comments", key=key)
                    page = await _gather(sem, call, "jira.list_issue_comments", {"issue_id_or_key": key, "start_at": len(got), "max_results": 100})
                    more = rows(page, "comments")
                    if not more:
                        break
                    got += more
                t["fields"]["comment"] = {"comments": got, "total": total}
            except Exception:  # noqa: BLE001
                pass

    await asyncio.gather(*(detail(n, t) for n, t in enumerate(found[:MAX_TICKETS])))
    out["jira"] = found[:MAX_TICKETS]


# ------------------------------------------------------------ Confluence
async def fetch_confluence(call: Call, since: str, log: ToolLog, out: dict) -> None:
    cql = f'type = page AND (creator = currentUser() OR contributor = currentUser()) AND lastModified >= "{since[:10]}" ORDER BY lastModified DESC'
    try:
        log.add("confluence.cql_search")
        data = await call("confluence.cql_search", {"cql": cql, "limit": 50, "expand": "content.version,content.history,content.metadata.labels"})
    except Exception as e:  # noqa: BLE001
        out["failed"].append({"source": "docs:confluence", "error": str(e)[:200]})
        return
    hits = rows(data, "results")[:MAX_DOCS_PER_SOURCE]
    sem = asyncio.Semaphore(PARALLEL)

    async def one(h: dict) -> None:
        c = h.get("content") if isinstance(h.get("content"), dict) else h
        pid = str(c.get("id") or h.get("id") or "")
        if not pid:
            return
        hist = c.get("history") or {}
        ver = c.get("version") or {}
        labels = [str(l.get("name", "")) for l in rows(((c.get("metadata") or {}).get("labels")) or {}, "results")]
        body = ""
        try:
            log.add("confluence.get_page_by_id", id=pid)
            page = await _gather(sem, call, "confluence.get_page_by_id", {"id": pid})
            body = text_of((page or {}).get("body") if isinstance(page, dict) and "body" in page else page)
        except Exception as e:  # noqa: BLE001
            out["failed"].append({"source": f"docs:confluence:{pid}", "error": str(e)[:120]})
        links = c.get("_links") or h.get("_links") or {}
        out["docs"].append({
            "_source": "confluence", "id": pid, "title": c.get("title") or h.get("title") or "", "body": body, "labels": labels,
            "author": ((hist.get("createdBy") or {}).get("displayName")) or "", "editors": [((ver.get("by") or {}).get("displayName")) or ""],
            "created": hist.get("createdDate") or "", "modified": h.get("lastModified") or ver.get("when") or hist.get("createdDate") or "",
            "url": h.get("url") or links.get("webui") or "", "_mine": True, "kind": "confluence page"})

    await asyncio.gather(*(one(h) for h in hits))
    if len(hits) >= MAX_DOCS_PER_SOURCE:
        out["capped"].append(f"confluence pages: first {MAX_DOCS_PER_SOURCE} only")


# ---------------------------------------------------------------- Notion
async def fetch_notion(call: Call, since: str, log: ToolLog, out: dict) -> None:
    me = ""
    try:
        log.add("notion.get_about_user")
        who = await call("notion.get_about_user", {})
        me = str((who or {}).get("id") or ((who or {}).get("bot") or {}).get("owner", {}).get("user", {}).get("id") or "") if isinstance(who, dict) else ""
    except Exception as e:  # noqa: BLE001
        out["failed"].append({"source": "docs:notion", "error": f"could not tell which pages are the user's: {str(e)[:120]}"})
        return
    pages: list[dict] = []
    cursor: str | None = None
    try:
        for _ in range(3):
            args: dict[str, Any] = {"query": "", "filter_value": "page", "filter_property": "object", "timestamp": "last_edited_time", "direction": "descending", "page_size": 100}
            if cursor:
                args["start_cursor"] = cursor
            log.add("notion.search_notion_page")
            data = await call("notion.search_notion_page", args)
            batch = rows(data, "results")
            pages += batch
            cursor = data.get("next_cursor") if isinstance(data, dict) else None
            if not cursor or not batch or str(batch[-1].get("last_edited_time", "9999")) < since:
                break
    except Exception as e:  # noqa: BLE001
        out["failed"].append({"source": "docs:notion", "error": str(e)[:200]})
        return

    def uid(x: Any) -> str:
        return str((x or {}).get("id", "")) if isinstance(x, dict) else ""

    mine = [p for p in pages if str(p.get("last_edited_time", "")) >= since and me and me in (uid(p.get("created_by")), uid(p.get("last_edited_by")))][:MAX_DOCS_PER_SOURCE]
    if not me:
        out["failed"].append({"source": "docs:notion", "error": "user id unknown, so no page could be attributed to the user"})
    sem = asyncio.Semaphore(PARALLEL)

    def title(p: dict) -> str:
        for v in (p.get("properties") or {}).values():
            if isinstance(v, dict) and v.get("type") == "title":
                return "".join(t.get("plain_text", "") for t in v.get("title", []) if isinstance(t, dict))
        return str(p.get("title") or "")

    async def one(p: dict) -> None:
        body = ""
        try:
            log.add("notion.get_page_markdown", id=p.get("id"))
            body = text_of(await _gather(sem, call, "notion.get_page_markdown", {"page_id": p.get("id")}))
        except Exception as e:  # noqa: BLE001
            out["failed"].append({"source": f"docs:notion:{p.get('id')}", "error": str(e)[:120]})
        out["docs"].append({"_source": "notion", "id": p.get("id"), "title": title(p), "body": body, "labels": [],
                            "author": "me" if uid(p.get("created_by")) == me else "", "editors": ["me"] if uid(p.get("last_edited_by")) == me else [],
                            "created": p.get("created_time", ""), "modified": p.get("last_edited_time", ""), "url": p.get("url", ""),
                            "_mine": True, "kind": "notion page"})

    await asyncio.gather(*(one(p) for p in mine))


# ----------------------------------------------------------- Google Drive
async def fetch_drive(call: Call, since: str, log: ToolLog, out: dict) -> None:
    q = ("trashed = false and mimeType = 'application/vnd.google-apps.document' "
         f"and modifiedTime > '{since[:10]}T00:00:00' and ('me' in owners or 'me' in writers)")
    try:
        log.add("drive.find_file")
        data = await call("drive.find_file", {"q": q, "pageSize": 100, "orderBy": "modifiedTime desc",
                                              "fields": "files(id,name,mimeType,createdTime,modifiedTime,webViewLink,description,owners(displayName,emailAddress),lastModifyingUser(displayName,emailAddress))"})
    except Exception as e:  # noqa: BLE001
        out["failed"].append({"source": "docs:drive", "error": str(e)[:200]})
        return
    files = rows(data, "files")[:MAX_DOCS_PER_SOURCE]
    sem = asyncio.Semaphore(PARALLEL)

    async def one(f: dict) -> None:
        body = ""
        try:
            log.add("drive.export_google_workspace_file", id=f.get("id"))
            body = text_of(await _gather(sem, call, "drive.export_google_workspace_file", {"fileId": f.get("id"), "mimeType": "text/plain"}))
        except Exception as e:  # noqa: BLE001
            out["failed"].append({"source": f"docs:drive:{f.get('id')}", "error": str(e)[:120]})
        owner = (f.get("owners") or [{}])[0]
        out["docs"].append({"_source": "drive", "id": f.get("id"), "title": f.get("name", ""), "body": (body + " " + (f.get("description") or "")).strip(), "labels": [],
                            "author": owner.get("displayName") or owner.get("emailAddress") or "", "editors": [(f.get("lastModifyingUser") or {}).get("displayName") or ""],
                            "created": f.get("createdTime", ""), "modified": f.get("modifiedTime", ""), "url": f.get("webViewLink", ""), "_mine": True, "kind": "google doc"})

    await asyncio.gather(*(one(f) for f in files))


# ------------------------------------------------------- Slack by search
def _matches(data: Any) -> list[dict]:
    if isinstance(data, dict):
        m = data.get("messages")
        if isinstance(m, dict):
            return rows(m, "matches")
        if isinstance(m, list):
            return rows(m)
        return rows(data, "matches")
    return rows(data, "matches")


async def fetch_slack_search(call: Call, picked: list[dict], since_ts: str, log: ToolLog, out: dict) -> None:
    """The user's own messages in the chosen channels only, then each thread they appear in. `picked` has already
    passed the DM guard; a hit from any other channel is dropped here too."""
    from guards import is_dm_channel
    by_id = {c["id"]: c for c in picked}
    after = since_ts[:10]
    threads: dict[tuple[str, str], None] = {}
    for ch in picked:
        name = str(ch.get("name") or "").lstrip("#")
        if not name or is_dm_channel(ch):
            continue  # DMs (opt-in) are read through history below, never through a name search
        try:
            log.add("slack.search_messages", channel=ch["id"], is_dm=False)
            data = await call("slack.search_messages", {"query": f"in:#{name} from:me after:{after}", "count": 100, "auto_paginate": True, "sort": "timestamp", "sort_dir": "asc"})
        except Exception as e:  # noqa: BLE001
            out["failed"].append({"source": f"slack-search:#{name}", "error": str(e)[:200]})
            continue
        hits = _matches(data)[:MAX_SLACK_HITS]
        if len(hits) >= MAX_SLACK_HITS:
            out["capped"].append(f"slack hits in #{name}: first {MAX_SLACK_HITS} only")
        for h in hits:
            cid = str((h.get("channel") or {}).get("id") or h.get("channel_id") or ch["id"]) if isinstance(h.get("channel"), dict) else str(h.get("channel") or ch["id"])
            if cid != ch["id"] or cid not in by_id:
                continue  # not the chosen channel
            if h.get("user"):
                out["slack"].setdefault("my_user_ids", [])
                if h["user"] not in out["slack"]["my_user_ids"]:
                    out["slack"]["my_user_ids"].append(h["user"])  # from:me hits are the connected user's own messages
            ts = str(h.get("thread_ts") or h.get("ts") or "")
            if ts:
                threads[(cid, ts)] = None
    sem = asyncio.Semaphore(PARALLEL)
    keys = list(threads)[:MAX_THREAD_FETCHES]
    if len(threads) > len(keys):
        out["capped"].append(f"slack threads: first {MAX_THREAD_FETCHES} only")

    async def one(cid: str, ts: str) -> None:
        try:
            log.add("slack.fetch_message_thread_from_a_conversation", channel=cid, is_dm=False)
            data = await _gather(sem, call, "slack.fetch_message_thread_from_a_conversation", {"channel": cid, "ts": ts, "limit": 200})
            msgs = rows(data, "messages")
        except Exception as e:  # noqa: BLE001
            out["failed"].append({"source": f"slack-thread:{cid}:{ts}", "error": str(e)[:120]})
            return
        if msgs:
            out["slack"]["threads"].append({"channel": by_id[cid].get("name", ""), "channel_id": cid, "messages": msgs})

    await asyncio.gather(*(one(c, t) for c, t in keys))
