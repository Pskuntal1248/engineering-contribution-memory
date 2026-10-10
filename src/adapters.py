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


def rows(data: Any, *keys: str, _depth: int = 0) -> list[dict]:
    """The list of records in a gateway payload. Tools wrap their result in different envelopes (data, response_data,
    results...), so named keys are tried first and then any nested envelope, a few levels deep."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict) and _depth < 5:
        for k in (*keys, "items", "data", "results", "values"):
            v = data.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                got = rows(v, *keys, _depth=_depth + 1)
                if got:
                    return got
        for k, v in data.items():  # unknown envelope: look inside it, skipping bookkeeping keys
            if isinstance(v, dict) and k not in {"properties", "metadata", "fields"}:
                got = rows(v, *keys, _depth=_depth + 1)
                if got:
                    return got
    return []


def find_key(data: Any, *names: str, _depth: int = 0) -> Any:
    """First value stored under any of `names`, searching nested envelopes."""
    if isinstance(data, dict) and _depth < 5:
        for n in names:
            if data.get(n) not in (None, ""):
                return data[n]
        for v in data.values():
            if isinstance(v, dict):
                got = find_key(v, *names, _depth=_depth + 1)
                if got not in (None, ""):
                    return got
    return None


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


def _tool_msg(err: str) -> str:
    """The readable part of a tool error: the JSON "message" when there is one, else the text, cut at a word."""
    from guards import clip
    m = re.search(r'"message"\s*:\s*"([^"]+)"', err or "")
    return clip((m.group(1) if m else err or "").strip(), 100)


def explain(source: str, err: str) -> str:
    """A reason a person can act on, from a tool error."""
    e = (err or "").lower()
    tm = _tool_msg(err or "")
    name = source.split(":")[-1].capitalize()
    if any(k in e for k in ("not enabled", "not installed", "no confluence", "not licensed", "unavailable", "no such app", "not available")):
        return f"{name} is not enabled on this site or workspace (tool said: {tm}). Remove it from docs_source or enable it."
    if "scope" in e and any(k in e for k in ("401", "403", "unauthor", "forbidden")):
        return f"{name} is connected but its permission scope does not match; reconnect with: gravity connect {source.split(':')[-1].lower()}"
    if any(k in e for k in ("needs_reconnect", "reconnect", "unauthor", "401", "403", "forbidden", "not connected", "no connection", "no account")):
        return f"{name} is not connected, or the connection needs renewing: run gravity connect for it (tool said: {tm})"
    if "404" in e or "not found" in e:
        return f"{name} was not found at that site (tool said: {tm})"
    return f"{name} could not be read: {tm}"


def failed_payload(data: Any) -> str:
    """The text of an error returned as data (success false / error key) instead of raised, else empty."""
    if isinstance(data, dict):
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        if data.get("successful") is False or data.get("success") is False or inner.get("successful") is False:
            return str(data.get("error") or inner.get("error") or data.get("message") or "the tool reported failure")
        if isinstance(data.get("error"), (str, dict)) and data.get("error") and not any(k in data for k in ("results", "values", "issues")):
            return str(data["error"])
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
            token = find_key(data, "next_page_token", "nextPageToken")
            if not token or len(found) >= MAX_TICKETS or find_key(data, "isLast") is True:
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
def _scope_problem(err: str) -> bool:
    e = (err or "").lower()
    return "scope" in e or "401" in e or "403" in e or "unauthor" in e or "forbidden" in e


_PROBES = (("confluence.who_am_i", {}), ("confluence.get_spaces", {"limit": 1}), ("confluence.search_pages", {"limit": 1}), ("confluence.get_pages", {"limit": 1}))


async def probe_confluence(call: Call, log: ToolLog) -> str:
    """When every read path is refused, try one tiny read per other Confluence tool so the message says which ones the connection allows."""
    res = []
    for name, args in _PROBES:
        try:
            log.add(name)
            data = await call(name, args)
            bad = failed_payload(data)
            res.append(f"{name.split('.')[1]}: " + ("refused" if bad else "ok"))
        except Exception:  # noqa: BLE001
            res.append(f"{name.split('.')[1]}: refused")
    return "Checked what the connection allows: " + ", ".join(res) + "."


async def fetch_confluence_v2(call: Call, since: str, log: ToolLog, out: dict, first_error: str) -> None:
    """Fallback when the CQL search (Confluence REST v1) is refused for scope: list recent pages through the v2 pages tool and keep
    the ones the connected user wrote or last edited. If that is refused too, say so plainly; the user needs to reconnect."""
    mine_ids: set[str] = set()
    my_name = ""
    try:  # who the connection is; some connections refuse this call while the pages list still works
        log.add("confluence.get_current_user")
        me = await call("confluence.get_current_user", {})
        acct = None if failed_payload(me) else find_key(me, "account_id", "accountId")
        mine_ids = {str(acct)} if acct else set()
        my_name = str(find_key(me, "display_name", "displayName", "public_name", "publicName") or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        log.add("confluence.get_pages")
        try:
            data = await call("confluence.get_pages", {"sort": "-modified-date", "limit": 100, "status": "current", "body_format": "storage"})
            if failed_payload(data):
                raise RuntimeError(failed_payload(data))
        except Exception:  # noqa: BLE001 - a plain list call, in case an option was refused
            data = await call("confluence.get_pages", {"limit": 100})
    except Exception as e:  # noqa: BLE001
        out["failed"].append({"source": "docs:confluence", "error": explain("docs:confluence", str(e)).rstrip(". ") + ". " + await probe_confluence(call, log)})
        return
    bad = failed_payload(data)
    if bad:
        out["failed"].append({"source": "docs:confluence", "error": explain("docs:confluence", bad).rstrip(". ") + ". " + await probe_confluence(call, log)})
        return
    listed = rows(data, "results")
    solo_note = False
    if not mine_ids:
        authors = {str(h.get("authorId") or "") for h in listed if h.get("authorId")}
        if len(authors) == 1:  # one author on every visible page: that is the connected account
            mine_ids, solo_note = set(authors), True
        else:
            out["failed"].append({"source": "docs:confluence", "error": "Confluence pages were listed but the connection does not say who you are, and the pages have several authors, so yours cannot be picked out."})
            return
    sem = asyncio.Semaphore(PARALLEL)

    async def labels_of(pid: str) -> list[str]:
        try:
            log.add("confluence.get_labels_for_page", id=pid)
            got = await _gather(sem, call, "confluence.get_labels_for_page", {"id": pid, "limit": 50})
            return [str(x.get("name", "")) for x in rows(got, "results")]
        except Exception:  # noqa: BLE001
            return []
    for h in rows(data, "results")[:MAX_DOCS_PER_SOURCE]:
        ver = h.get("version") or {}
        modified = str(ver.get("createdAt") or h.get("createdAt") or "")
        if modified[:10] and modified[:10] < since[:10]:
            continue
        author_id, editor_id = str(h.get("authorId") or ""), str(ver.get("authorId") or "")
        if author_id not in mine_ids and editor_id not in mine_ids:
            continue
        links = h.get("_links") or {}
        base = str((find_key(data, "base") or "")).rstrip("/")
        web = str(links.get("webui") or "")
        out["docs"].append({
            "_source": "confluence", "id": str(h.get("id", "")), "title": h.get("title") or "", "body": text_of((h.get("body") or {}).get("storage") or {}),
            "labels": await labels_of(str(h.get("id", ""))), "author": my_name if author_id in mine_ids else "", "editors": [my_name] if editor_id in mine_ids else [],
            "created": str(h.get("createdAt") or ""), "modified": modified, "url": (base + web) if web.startswith("/") and base else web,
            "_mine": True, "kind": "confluence page"})
    out.setdefault("capped", []).append("confluence: read through the pages list (the CQL search was refused); only the 100 most recently edited pages were checked"
                                        + ("; the connection did not name you, so the pages are those of the one author visible" if solo_note else ""))


async def fetch_confluence(call: Call, since: str, log: ToolLog, out: dict) -> None:
    cql = f'type = page AND (creator = currentUser() OR contributor = currentUser()) AND lastModified >= "{since[:10]}" ORDER BY lastModified DESC'
    try:
        log.add("confluence.cql_search")
        data = await call("confluence.cql_search", {"cql": cql, "limit": 50, "expand": "content.version,content.history,content.metadata.labels"})
    except Exception as e:  # noqa: BLE001
        if _scope_problem(str(e)):
            await fetch_confluence_v2(call, since, log, out, str(e))
            return
        out["failed"].append({"source": "docs:confluence", "error": explain("docs:confluence", str(e))})
        return
    bad = failed_payload(data)
    if bad:
        if _scope_problem(bad):
            await fetch_confluence_v2(call, since, log, out, bad)
            return
        out["failed"].append({"source": "docs:confluence", "error": explain("docs:confluence", bad)})
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
async def notion_user_id(call: Call, names: list[str], log: ToolLog, out: dict) -> tuple[str, bool]:
    """The connected user's Notion id. notion.get_about_user needs an id, so list the workspace's people and pick the one
    whose name or email matches the user's names; if the workspace has exactly one person, that is the user."""
    try:
        log.add("notion.list_users")
        users = rows(await call("notion.list_users", {"page_size": 100}), "results", "users")
    except Exception as e:  # noqa: BLE001
        out["failed"].append({"source": "docs:notion", "error": f"could not list Notion users to find yours: {str(e)[:120]}"})
        return "", False
    people = [u for u in users if str(u.get("type", "person")) == "person" and str(u.get("object", "user")) == "user"]
    want = [n.lower() for n in names if n]
    for u in people:
        label = " ".join([str(u.get("name") or ""), str(((u.get("person") or {}).get("email")) or "")]).lower()
        if any(w and (w in label or label.split("@")[0].strip() == w) for w in want):
            return str(u.get("id", "")), len(people) == 1
    if len(people) == 1:
        return str(people[0].get("id", "")), True
    out["failed"].append({"source": "docs:notion", "error": f"could not tell which of {len(people)} Notion users is you: set display_name to your Notion name"})
    return "", False


async def fetch_notion(call: Call, since: str, log: ToolLog, out: dict, names: list[str] | None = None) -> None:
    me, solo = await notion_user_id(call, names or [], log, out)
    if not me:
        return
    if solo:  # one person in the workspace: pages made through an integration on their behalf are theirs too
        out["capped"].append("notion: single-person workspace, so every recently edited page is attributed to you")
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
            cursor = find_key(data, "next_cursor")
            if not cursor or not batch or str(batch[-1].get("last_edited_time", "9999")) < since:
                break
    except Exception as e:  # noqa: BLE001
        out["failed"].append({"source": "docs:notion", "error": str(e)[:200]})
        return

    def uid(x: Any) -> str:
        return str((x or {}).get("id", "")) if isinstance(x, dict) else ""

    mine = [p for p in pages if str(p.get("last_edited_time", "")) >= since and (solo or me in (uid(p.get("created_by")), uid(p.get("last_edited_by"))))][:MAX_DOCS_PER_SOURCE]
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
                            "author": (names or ["me"])[0] if (solo or uid(p.get("created_by")) == me) else "", "editors": [(names or ["me"])[0]] if (solo or uid(p.get("last_edited_by")) == me) else [],
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
    got = find_key(data, "matches")
    if isinstance(got, list):
        return [x for x in got if isinstance(x, dict)]
    return rows(data, "matches", "messages")


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
            mb = re.match(r"(https://[^/]+)/archives/", str(h.get("permalink") or ""))
            if mb:
                out["slack"]["permalink_base"] = mb.group(1)
            ts = str(h.get("thread_ts") or "")
            if not ts:  # search hits may only carry the thread in the permalink (?thread_ts=...); a reply must fetch its whole thread
                mt = re.search(r"thread_ts=(\d+\.\d+)", str(h.get("permalink") or ""))
                ts = mt.group(1) if mt else str(h.get("ts") or "")
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
