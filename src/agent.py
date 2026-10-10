"""UC-10 Engineering Contribution Memory. Read-only. Plain LangGraph, async graph().

Pipeline: load memory -> fetch sources -> normalize/dedupe/filter -> link + analyse -> render -> save memory.
Code does the facts and enforces the rules; the model (if used) only labels and words, and its output is
validated against real item ids."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

import eprofile
import outputs
from guards import ToolLog, budget_for, internal_terms, parse_channels
from models import Item
from pipeline import Config, ingest
from sources import GatewaySource, Source
from store import NativeMemoryStore, PlatformStore, ProfileStore, platform_store
from ask import parse_request, parse_team_changes
from weekly import new_items, next_cursors, quiet_week_line, since_for, id_key

ON_DEMAND = {"weekly_summary", "one_on_one", "review_material", "interview_stories", "evidence_gaps"}


class State(TypedDict, total=False):
    inputs: dict
    memory: dict
    ctx: dict
    corpus: dict
    result: dict


def _payload(raw: Any) -> Any:
    """A tool result as data. Gateway refusals arrive as text, not JSON: raise, never pass them on."""
    if isinstance(raw, list):
        raw = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in raw)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError(raw[:300]) from None
    if isinstance(raw, dict) and raw.get("successful") is False:
        raise RuntimeError(str(raw.get("error"))[:300])
    return raw.get("data", raw) if isinstance(raw, dict) else raw


class Deps:
    """Everything the graph needs from outside. Tests pass fixtures and a fake model; graph() passes the gateway."""

    def __init__(self, source_factory, llm=None, store_factory=None, now=None, embedder=None):
        self.source_factory, self.llm, self.store_factory, self.embedder = source_factory, llm, store_factory, embedder
        self.now = now or (lambda: datetime.now(timezone.utc))


def _docs_sources(raw: str) -> list[str]:
    if raw.strip().lower() == "all three":
        return ["confluence", "notion", "google drive"]
    return [d.lower() for d in (x.strip() for x in raw.replace("+", ",").split(",")) if d and d.lower() != "none"]


def _cfg(inp: dict) -> Config:
    user = str(inp.get("github_username", "")).strip()
    return Config(
        username=user, repos=[r.strip() for r in str(inp.get("repos_or_org", "")).replace("\n", ",").split(",") if r.strip()],
        jira_site=str(inp.get("jira_site", "")), jira_projects=[p.strip().upper() for p in str(inp.get("jira_projects", "")).replace("\n", ",").split(",") if p.strip()],
        docs_sources=_docs_sources(str(inp.get("docs_source", ""))),
        slack_channels=parse_channels(str(inp.get("slack_channels", ""))), include_dms=str(inp.get("include_dms", "No")).lower() in {"yes", "true"},
        slack_user_ids=set(parse_channels(str(inp.get("slack_user_ids", "")))), name_hints=[user, *parse_channels(str(inp.get("display_name", "")))],
    )


def make_graph(deps: Deps):
    async def load(state: State) -> dict:
        inp = state.get("inputs", {})
        mem = state.get("memory")
        if deps.store_factory:
            store = deps.store_factory(mem)
        else:
            ps = platform_store()
            store = PlatformStore(ps, mem) if ps is not None else NativeMemoryStore(mem, 256)
        prior = await store.aload() if hasattr(store, "aload") else store.load()
        req = str(inp.get("request") or "auto")
        said, d1, d2 = parse_request(str(inp.get("ask") or ""), deps.now().date())
        if said and req == "auto":
            req = said  # the typed request wins over the dropdown's default
        if d1 or d2:
            inp["period_from"] = str(inp.get("period_from") or d1)
            inp["period_to"] = str(inp.get("period_to") or d2)
        has_memory = bool(prior.get("items") or prior.get("last_run"))  # an empty but completed run still counts
        if req == "auto":
            req = "weekly" if prior.get("backfill_done") else "backfill"  # weekly only after a backfill really completed
        if req in ON_DEMAND and not has_memory:
            req = "backfill"  # nothing to report from yet
        return {"ctx": {"started": time.monotonic(), "mode": req, "prior": prior, "store": store, "notes": ["memory was unreadable, starting fresh"] if prior.get("_corrupt") else (["the saved history was missing from the store, so this run backfills again"] if prior.get("_store_empty") else [])}}

    async def fetch(state: State) -> dict:
        ctx = state["ctx"]
        inp = state["inputs"]
        cfg = _cfg(inp)
        ctx["cfg"], ctx["log"] = cfg, ToolLog()
        mode = ctx["mode"]
        if mode in ON_DEMAND:  # answers come from the saved profile, no source reads
            ctx["raw"] = {"failed": [], "capped": []}
            return {"ctx": ctx}
        months = int(inp.get("backfill_months") or 6)
        start = (deps.now() - timedelta(days=30 * months)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cursor = (ctx["prior"].get("cursors") or {}).get("github")
        since = since_for(cursor, start) if mode == "weekly" else start
        ctx["since"] = since
        src: Source = deps.source_factory(cfg)
        ctx["raw"] = await src.fetch(since, None, ctx["log"])
        return {"ctx": ctx}

    async def analyse(state: State) -> dict:
        ctx, inp, cfg = state["ctx"], state["inputs"], state["ctx"]["cfg"]
        mode, prior = ctx["mode"], ctx["prior"]
        names = {n for n in cfg.name_hints if n}
        budget = budget_for("backfill" if mode == "backfill" else "weekly" if mode == "weekly" else "on_demand")
        budget.started = ctx.get("started", budget.started)  # the spec's time limit covers the whole run, source reads included
        old = [eprofile.expand(r) for r in prior.get("items", [])]
        new_ids: set[str] = set()
        notes = list(ctx["notes"])
        failed = list(ctx["raw"].get("failed", []))
        notes += [f"capped: {c}" for c in dict.fromkeys(ctx["raw"].get("capped", []))]
        if mode in {"backfill", "weekly"}:
            ing = ingest(ctx["raw"], cfg, ctx["log"])
            seen = set(prior.get("seen", []))
            fresh, already = new_items(ing.items, seen) if mode == "weekly" else (ing.items, 0)
            new_ids = {i.id for i in fresh}
            have = {i.id for i in old}
            items = old + [i for i in fresh if i.id not in have]
            if ing.skipped:
                why: dict[str, int] = {}
                for sk in ing.skipped:
                    why[sk.reason] = why.get(sk.reason, 0) + 1
                top = sorted(why.items(), key=lambda kv: -kv[1])
                notes.append(f"Rows not counted ({len(ing.skipped)}): " + "; ".join(f"{r} ({n})" for r, n in top[:12]) + ".")
            if ing.not_found_channels:
                known = {f.get("source") for f in failed}
                failed += [{"source": f"slack:#{c}", "error": "channel not found"} for c in ing.not_found_channels if f"slack:#{c}" not in known]
        else:
            items = old
        ctx["items"], ctx["new_ids"], ctx["failed"] = items, new_ids, failed
        prof = await eprofile.build(items, username=cfg.username, names=names, llm=deps.llm, budget=budget,
                                    framework=str(inp.get("review_framework", "") or ""), role_and_level=str(inp.get("role_and_level", "")),
                                    prior=prior, repo_aliases=cfg.repo_aliases, embedder=deps.embedder,
                                    team_changes=parse_team_changes(str(inp.get("team_changes", "") or "")), reuse_names=mode != "backfill")
        notes += prof["notes"]
        ctx["prof"], ctx["notes"], ctx["budget"] = prof, notes, budget
        return {"ctx": ctx}

    async def render(state: State) -> dict:
        ctx, inp, cfg = state["ctx"], state["inputs"], state["ctx"]["cfg"]
        items, prof, mode = ctx["items"], ctx["prof"], ctx["mode"]
        names = {n for n in cfg.name_hints if n}
        colleagues = sorted({a for i in items for a in [i.author, *i.co_authors, *(i.meta.get("participants") or [])] if a and a not in names and a.lower() != cfg.username.lower()})
        terms = internal_terms(items, [t for t in str(inp.get("redact_terms", "")).replace("\n", ",").split(",")], cfg.repos[0].split("/")[0] if cfg.repos else "", cfg.jira_site)
        window = ctx["new_ids"]
        if mode == "weekly_summary":  # on request: the last 7 days of the saved profile
            cut = (deps.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            window = {i.id for i in items if i.date[:10] >= cut}
        since = ctx.get("since", "")[:10] or (deps.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        window_label = f"dated since {cut} (the last 7 days)" if mode == "weekly_summary" else "new since the last run"
        quiet = quiet_week_line(since, deps.now().strftime("%Y-%m-%d")) if mode in {"weekly", "weekly_summary"} and not window else None
        title = {"backfill": "Engineering profile", "weekly": "Weekly update", "weekly_summary": "Weekly summary", "one_on_one": "1:1 talking points",
                 "review_material": "Review material", "interview_stories": "Interview stories", "evidence_gaps": "Evidence gaps"}[mode]
        corpus, result = outputs.corpus_for(mode, prof, items, title=f"{title} - {cfg.username}", terms=terms, keep_internal=str(inp.get("keep_internal_details", "No")).lower() == "yes",
                                            colleagues=colleagues, names=names, date_from=str(inp.get("period_from", "") or ""), date_to=str(inp.get("period_to", "") or ""),
                                            run_notes=ctx["notes"], failed=ctx["failed"], quiet_line=quiet, new_ids=window, window_label=window_label)
        result["model_calls"] = ctx["budget"].calls
        result["seconds"] = round(ctx["budget"].elapsed(), 1)
        result["new_items"] = len(ctx["new_ids"])
        wanted_docs = _docs_sources(str(inp.get("docs_source", "")))
        result["docs_sources_asked"] = wanted_docs
        def _doc_src(i: Any) -> str:
            parts = i.id.split(":")
            return {"drive": "google drive"}.get(parts[1].lower(), parts[1].lower()) if len(parts) >= 3 else ""
        result["docs_items_by_source"] = {s_: sum(1 for i in items if i.type == "doc" and _doc_src(i) == s_) for s_ in wanted_docs}
        result["dm_reads"] = len(ctx["log"].dm_reads())
        # The report itself is the corpus (the page Gravity shows in full) plus the ledger table (CSV download). The result box only
        # gets a short summary: the headline, the section list and the ledger size. No long text there, so nothing looks cut off.
        result["page_summary"] = corpus.get("summary", "")
        result["page_sections"] = [str(sec.get("heading", "")) for sec in corpus.get("sections", [])]
        result["page_chars"] = len(outputs.to_markdown(corpus, with_tables=False))
        outputs.write_local_copy(corpus, result=result)
        ctx["corpus"], ctx["result"] = corpus, result
        return {"ctx": ctx, "corpus": corpus, "result": result}

    async def persist(state: State) -> dict:
        ctx = state["ctx"]
        if ctx["mode"] in ON_DEMAND:
            return {}  # on-demand answers never change the saved profile
        prof, items, prior = ctx["prof"], ctx["items"], ctx["prior"]
        po = prof["project_of"]
        profile = {
            "items": [eprofile.compact(i, po.get(i.id, "")) for i in sorted(items, key=lambda x: (x.date, x.id))],
            "seen": sorted(set(prior.get("seen", [])) | {id_key(i.id) for i in items} | {id_key(m) for i in items for m in i.meta.get("merged_ids", [])}),
            "cursors": next_cursors(prior.get("cursors", {}), [i for i in items if i.source]),
            "verdicts": prof["verdicts"], "cards": prof["card_store"], "dverdicts": prof.get("dverdicts", {}), "last_run": deps.now().strftime("%Y-%m-%d"),
            "backfill_done": bool(prior.get("backfill_done")) or ctx["mode"] == "backfill",
            "gaps": [{"id": g["id"], "status": g["status"]} for g in prof["gaps"]],
        }
        store = ctx["store"]
        memory = await store.adump(profile) if hasattr(store, "adump") else store.dump(profile)
        # memory is written only here, after the work above succeeded
        return {"memory": memory}

    wf = StateGraph(State)
    for n, f in (("load", load), ("fetch", fetch), ("analyse", analyse), ("render", render), ("persist", persist)):
        wf.add_node(n, f)
    wf.set_entry_point("load")
    for a, b in (("load", "fetch"), ("fetch", "analyse"), ("analyse", "render"), ("render", "persist"), ("persist", END)):
        wf.add_edge(a, b)
    return wf.compile()


async def graph():
    # Environment variables are read here, inside the function, never at import time.
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from llm import GatewayLLM

    token = os.environ["GRAVITY_RUN_TOKEN"]
    client = MultiServerMCPClient({"gravity": {"transport": "streamable_http", "url": os.environ["GRAVITY_GATEWAY_URL"],
                                               "headers": {"Authorization": f"Bearer {token}"}}})
    tools = {t.name: t for t in await client.get_tools()}  # only the tools declared in manifest.yaml

    async def call(name: str, args: dict):
        tool = tools.get(name.replace(".", "_"))
        if tool is None:
            raise RuntimeError(f"{name} is not allowed in this run")
        return _payload(await tool.ainvoke(args))

    has = {k.replace("_", ".", 1) for k in tools}
    llm = GatewayLLM(os.environ["GRAVITY_LLM_URL"], token)
    from embed import GatewayEmbedder
    embedder = GatewayEmbedder(os.environ["GRAVITY_LLM_URL"], token)

    def source_factory(cfg: Config):
        return GatewaySource(call, cfg.username, cfg.repos, cfg.slack_channels, cfg.include_dms, cfg.jira_projects, has, cfg.docs_sources, cfg.name_hints)

    return make_graph(Deps(source_factory, llm, embedder=embedder))
