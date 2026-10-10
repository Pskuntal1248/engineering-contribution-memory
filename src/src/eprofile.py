"""Build and update the engineering profile from items. Deterministic core + optional model passes."""
from __future__ import annotations

from typing import Any

import analysis
import linking
import metrics
from guards import Budget, Statement, cap_role, confidential_ids, drop_colleague_assessments, keep_cited, predicts_promotion
from ladder import Expectation, map_evidence, parse_framework, statuses
from models import Item
from weekly import id_key

TEXT_KEEP = 160


def compact(it: Item, project: str) -> dict[str, Any]:
    m = it.meta
    keep = {k: m[k] for k in ("mine", "confidential", "repo", "number", "merged_flag", "reverted", "is_revert", "received", "reopen_count", "status", "channel", "my_messages", "cycle_hours", "participants") if k in m}
    keep["size"] = (m.get("size") or {}).get("lines") if m.get("size") else None
    return {"id": it.id, "s": it.source, "t": it.type, "date": it.date, "title": it.title[:90], "url": it.url, "text": it.text[:TEXT_KEEP],
            "author": it.author, "co": it.co_authors[:4], "keys": it.keys[:5], "p": project, "m": keep}


def expand(row: dict[str, Any]) -> Item:
    m = dict(row.get("m", {}))
    if m.get("size") is not None:
        m["size"] = {"lines": m["size"], "excluded": 0, "files": 0, "basis": "stored"}
    return Item(id=row["id"], source=row["s"], type=row["t"], date=row["date"], title=row["title"], url=row["url"], text=row["text"],
                author=row.get("author", ""), co_authors=row.get("co", []), keys=row.get("keys", []), meta=m)


async def build(items: list[Item], *, username: str, names: set[str], llm, budget: Budget, framework: str, role_and_level: str,
                prior: dict[str, Any] | None, repo_aliases: dict[str, str] | None = None) -> dict[str, Any]:
    by_id = {i.id: i for i in items}
    clusters = linking.link(items, repo_aliases or {})
    links = {i: r for c in clusters.values() for i, r in c["links"].items()}
    project_of = {iid: pid for pid, c in clusters.items() for iid in c["item_ids"]}
    notes: list[str] = []
    prior_ver = (prior or {}).get("verdicts", {})

    # invisible work / thread verdicts (model only for threads we have no verdict for)
    cand = [i for i in items if i.type == "thread" and i.meta.get("mine") and not analysis.has_strong_link(i, links) and i.id not in prior_ver]
    verdicts, n1 = await analysis.classify_threads_with_model(llm, budget, cand, names)
    notes += n1
    allv = {k: analysis.ThreadVerdict(**v) for k, v in prior_ver.items()}
    allv.update(verdicts)
    invisible = analysis.find_invisible(items, links, names, allv)
    decisions = analysis.find_decisions(items, names, username)

    # projects (only those with real work, i.e. 2+ items), cards by model or rules
    base = [analysis.baseline_project_card(pid, c["item_ids"], by_id, username) for pid, c in sorted(clusters.items()) if len(c["item_ids"]) >= 5]
    cards, n2 = await analysis.project_cards_with_model(llm, budget, base, by_id, username)
    notes += n2
    for card in cards:
        prs = [by_id[e] for e in card["evidence"] if by_id[e].type == "pr"]
        card["co_contributors"] = [n for n in {p for e in card["evidence"] for p in ([by_id[e].author] + by_id[e].co_authors)} if n and n.lower() != username.lower() and n not in names][:6]
        card["counts"] = {t: sum(1 for e in clusters[card["project_id"]]["item_ids"] if by_id[e].type == t) for t in ("pr", "review", "ticket", "thread", "doc")}
    stats = metrics.pr_stats(items)
    improv = metrics.improvement_areas(items)
    skills = analysis.map_skills(items)

    lab = {v["thread"]: v["label"] for v in invisible}
    for i in items:
        if i.type == "thread":
            i.meta["label"] = lab.get(i.id, "")
    gaps: list[dict] = []
    exps: list[Expectation] = parse_framework(framework, role_and_level) if framework.strip() else []
    if exps:
        known = set(by_id)
        ev = {e.id: map_evidence(e, items) for e in exps}
        gaps = statuses(exps, ev, known)
    else:
        notes.append("no review framework given: evidence gaps skipped; a framework would enable them")

    projects_clean = []
    for c in cards:
        projects_clean.append(c)
    team_ev = [i for i in items if i.meta.get("confidential")]
    return {
        "projects": projects_clean, "invisible": invisible, "decisions": decisions, "skills": skills, "improvements": improv,
        "gaps": gaps, "stats": stats, "notes": notes, "project_of": project_of,
        "verdicts": {k: v.model_dump() for k, v in allv.items()},
        "confidential": sorted(confidential_ids(items)),
        "top": [{"id": i.id, "title": i.title, "lines": metrics.contribution_size(i)} for i in metrics.top_contributions(items, 8)],
    }
