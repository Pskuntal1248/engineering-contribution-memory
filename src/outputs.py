"""Corpus + result builders. Every number comes from the profile (computed in code); prose is templated
from facts, optionally reworded by the model with evidence ids that code validates."""
from __future__ import annotations

from typing import Any

import ledger
from guards import FLAG, Statement, drop_colleague_assessments, flag_confidential, internal_terms, keep_cited, predicts_promotion, scrub
from metrics import shipped
from models import Item

LEDGER_CAP = 6000


def _link(it: Item | None) -> str:
    return it.url if it and it.url else (it.id if it else "")


_PRI = {"pr": 0, "ticket": 1, "doc": 2, "commit": 3, "review": 4, "thread": 5}


def _cite(ids: list[str], by_id: dict[str, Item], n: int = 3) -> str:
    out = []
    ids = sorted((i for i in ids if i in by_id), key=lambda i: (_PRI.get(by_id[i].type, 9), by_id[i].date, i))
    for i in ids[:n]:
        it = by_id.get(i)
        if it:
            out.append(f"{it.title[:48]} ({it.url or it.id})")
    return "; ".join(out)


def sources_for(items: list[Item]) -> list[dict[str, str]]:
    return [{"id": i.id, "title": i.title[:100], "url": i.url} for i in items if i.url][:300]


def build_statements(profile: dict[str, Any], by_id: dict[str, Item], kind: str, names: set[str], colleagues: list[str],
                     date_from: str = "", date_to: str = "") -> list[Statement]:
    """Deterministic statements from the profile, one per fact, each with evidence ids."""
    def within(ids: list[str]) -> bool:
        if not (date_from or date_to):
            return True
        ds = [by_id[i].date[:10] for i in ids if i in by_id]
        return any((not date_from or d >= date_from) and (not date_to or d <= date_to) for d in ds)

    sts: list[Statement] = []
    if kind in {"weekly_summary", "one_on_one", "review_material", "interview_stories"}:
        for p in profile["projects"]:
            if within(p["evidence"]):
                co = f" with {', '.join(p['co_contributors'][:3])}" if p.get("co_contributors") else ""
                sts.append(Statement(f"{p['name']}: {p['outcome']}; role {p['role']}{co}.", p["evidence"][:6], p["role"]))
    for v in profile["invisible"]:
        if within(v["evidence"]):
            sts.append(Statement(f"Not in any ticket ({v['label'].replace('_',' ')}): {v['summary']}", v["evidence"], "supported"))
    for d in profile["decisions"]:
        if within(d["evidence"]):
            sts.append(Statement(f"Decision ({d['role']}): {d['title']}. {d['summary']}", d["evidence"], d["role"]))
    for a in profile["improvements"]:
        sts.append(Statement(f"Recurring feedback, {a['count']} instances: {a['label']}.", a["item_ids"][:8]))
    conf = set(profile.get("confidential", []))
    sts = [flag_confidential(s, conf) for s in sts]
    sts, _ = keep_cited(sts, set(by_id))
    sts, _ = drop_colleague_assessments(sts, colleagues)
    sts = [s for s in sts if not predicts_promotion(s.text)]
    return sts


def one_on_one(profile: dict[str, Any], by_id: dict[str, Item], sts: list[Statement]) -> list[dict[str, Any]]:
    wins = [s for s in sts if s.text and not s.text.startswith(("Not in any ticket", "Decision", "Recurring"))][:4]
    unseen = [s for s in sts if s.text.startswith(("Not in any ticket", f"{FLAG} Not in any ticket"))][:4]
    decs = [s for s in sts if "Decision (" in s.text][:3]
    imp = [s for s in sts if s.text.startswith("Recurring")][:2]
    gaps = [g for g in profile["gaps"] if g["status"] != "backed" and g["level"] == "next"]
    mine = [i for i in by_id.values() if i.meta.get("mine")]
    blockers = [f"Open PR, not merged yet: {i.title[:80]} ({i.url or i.id})" for i in sorted(mine, key=lambda x: x.date)
                if i.type == "pr" and i.meta.get("state") == "open" and not i.meta.get("is_revert")][:4]
    blockers += [f"Ticket reopened {i.meta.get('reopen_count')}x: {i.title[:80]} ({i.url or i.id})" for i in sorted(mine, key=lambda x: x.date)
                 if i.type == "ticket" and i.meta.get("reopen_count", 0) > 0][:3]
    out = [
        {"heading": "Wins", "bullets": [f"{s.text} [{_cite(s.evidence, by_id, 1)}]" for s in wins]},
        {"heading": "You may not have seen", "bullets": [f"{s.text} [{_cite(s.evidence, by_id, 1)}]" for s in unseen]},
        {"heading": "Blockers", "bullets": blockers},
        {"heading": "Decisions needing input", "bullets": [f"{s.text} [{_cite(s.evidence, by_id, 1)}]" for s in decs]},
        {"heading": "Working on", "bullets": [f"{s.text} [{_cite(s.evidence, by_id, 2)}]" for s in imp]},
    ]
    if gaps:
        out.append({"heading": "For the next level", "bullets": [f"{g['expectation']}: {'no evidence yet' if g['status']=='missing' else str(len(g['evidence']))+' item(s), thin'}" for g in gaps[:4]]})
    return [s for s in out if s["bullets"]]


def weekly_sections(profile: dict[str, Any], by_id: dict[str, Item], ids: set[str], quiet_line: str | None) -> list[dict[str, Any]]:
    """Weekly summary: what shipped, what is in progress, problems solved, help given. Items from the window only; every line links."""
    if quiet_line or not ids:
        return [{"heading": "Nothing new this week", "body": quiet_line or "Nothing new this week."}]
    fresh = sorted((by_id[i] for i in ids if i in by_id and by_id[i].meta.get("mine", True)), key=lambda x: (x.date, x.id))

    def line(i: Item) -> str:
        return f"{i.title[:90]} ({i.url or i.id})"
    shipped_ = [i for i in fresh if (i.type == "pr" and shipped(i)) or (i.type == "commit" and not i.meta.get("is_revert") and not i.meta.get("reverted"))]
    prog = [i for i in fresh if i.type == "pr" and not shipped(i) and not i.meta.get("reverted") and not i.meta.get("is_revert") and i.meta.get("state") != "closed"]
    def from_window(labels: set[str]) -> list[str]:
        return [f"{v['summary']} ({_cite(v['evidence'], by_id, 1)})" for v in profile["invisible"] if v["label"] in labels and any(e in ids for e in v["evidence"])]
    return [
        {"heading": "What changed this week", "facts": [{"label": "New items", "value": len(fresh), "source": "ledger"}]},
        {"heading": "Shipped", "bullets": [line(i) for i in shipped_[:25]]},
        {"heading": "In progress", "bullets": [line(i) for i in prog[:15]]},
        {"heading": "Problems solved", "bullets": from_window({"problem_solved"})},
        {"heading": "Help given", "bullets": from_window({"help_given", "mentoring"})},
    ]


def stories(profile: dict[str, Any], by_id: dict[str, Item], terms: set[str], keep_internal: bool) -> list[dict[str, Any]]:
    """5-8 stories from problems solved, decisions and the biggest projects; internal names and figures stripped."""
    pool: list[tuple[str, str, list[str], str]] = []
    for v in profile["invisible"]:
        if v["label"] == "problem_solved":
            pool.append(("A hard bug", v["summary"], v["evidence"], "Tell me about a hard bug you debugged."))
    for d in profile["decisions"]:
        pool.append(("A decision with trade-offs", d["summary"], d["evidence"], "Tell me about a technical decision you made and its trade-offs."))
    for v in profile["invisible"]:
        if v["label"] in {"mentoring", "help_given"}:
            pool.append(("Helping a teammate", v["summary"], v["evidence"], "Tell me about a time you helped someone unblock."))
    for p in sorted(profile["projects"], key=lambda p: -len(p["evidence"]))[:3]:
        pool.append((f"Delivering {p['name']}", f"{p['outcome']}; goal: {p['goal']}", p["evidence"][:6], "Tell me about a project you delivered end to end."))
    out, seen, per_title = [], set(), {}
    for title, body, ev, q in pool:
        if body in seen or per_title.get(title, 0) >= 2:
            continue
        seen.add(body)
        per_title[title] = per_title.get(title, 0) + 1
        conf = [e for e in ev if e in set(profile.get("confidential", []))]
        text = scrub(body, terms, keep_internal)
        out.append({"title": title, "story": ((FLAG + " ") if conf else "") + text, "question": q, "evidence": ev[:4]})
        if len(out) == 8:
            break
    return out


def corpus_for(kind: str, profile: dict[str, Any], items: list[Item], *, title: str, terms: set[str], keep_internal: bool,
               colleagues: list[str], names: set[str], date_from: str, date_to: str, run_notes: list[str],
               failed: list[dict], quiet_line: str | None, new_ids: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    by_id = {i.id: i for i in items}
    s = profile["stats"]
    rows, trunc = ledger.rows(items, profile["project_of"], LEDGER_CAP)
    pname = {p["project_id"]: p["name"] for p in profile["projects"]}
    rows = [[r[0], r[1], pname.get(r[2], "(other work)" if r[2] != "(unlinked)" else r[2]), *r[3:]] for r in rows]
    ledger_sec = {"heading": "Item ledger: every item, with source, date, project and type",
                  "body": f"{len(rows)} items" + (f" (newest {LEDGER_CAP} of {len(items)})" if trunc else "") + ". Reverts and reverted PRs are listed but not counted as shipped.",
                  "table": {"columns": ledger.COLUMNS, "rows": rows}}
    facts = [{"label": "PRs shipped", "value": s["prs_shipped"], "source": "ledger"}, {"label": "PRs reverted (not counted)", "value": s["prs_reverted"], "source": "ledger"},
             {"label": "Commits authored (outside merged PRs)", "value": s["commits_authored"], "source": "ledger"},
             {"label": "Reviews given", "value": s["reviews_given"], "source": "ledger"}, {"label": "Tickets touched", "value": s["tickets_touched"], "source": "ledger"},
             {"label": "Median PR cycle (hours)", "value": s["median_cycle_hours"] if s["median_cycle_hours"] is not None else "n/a", "source": "ledger"}]
    sections: list[dict[str, Any]] = [ledger_sec]
    sts = build_statements(profile, by_id, kind if kind not in {"backfill", "weekly"} else "profile", names, colleagues, date_from, date_to)
    if kind == "weekly_summary" or kind == "weekly":
        sections[0:0] = weekly_sections(profile, by_id, new_ids, quiet_line)
    if kind in {"backfill", "profile"}:
        sections += [
            {"heading": "Projects", "bullets": [f"{p['name']} ({p['first']} to {p['last']}), role {p['role']}: {p['outcome']}. Evidence: {_cite(p['evidence'], by_id, 3)}" for p in profile["projects"]]},
            {"heading": "Problems solved", "bullets": [f"{v['summary']} ({_cite(v['evidence'], by_id, 1)})" for v in profile["invisible"] if v["label"] == "problem_solved"]},
            {"heading": "Technical decisions", "bullets": [f"{d['title']}, role {d['role']} ({_cite(d['evidence'], by_id, 1)})" for d in profile["decisions"]]},
            {"heading": "Invisible work: no ticket or PR", "bullets": [f"{v['label'].replace('_',' ')}: {v['summary']} ({_cite(v['evidence'], by_id, 1)})" for v in profile["invisible"]]},
            {"heading": "Skills with evidence", "bullets": [f"{k['skill']}: {k['count']} items, e.g. {_cite(k['evidence'], by_id, 2)}" for k in profile["skills"]]},
            {"heading": "Improvement areas", "bullets": [f"{a['label']}: {a['count']} instances ({_cite(a['item_ids'], by_id, 3)})" for a in profile["improvements"]]},
        ]
    if kind in {"backfill", "profile", "evidence_gaps"}:
        gaps = profile["gaps"]
        if gaps:
            sections.append({"heading": "Evidence gaps for the next level", "body": "What the evidence shows, not a prediction.",
                             "table": {"columns": ["Expectation", "Level", "Status", "Evidence"], "rows": [[g["expectation"], g["level"], g["status"], _cite(g["evidence"], by_id, 3)] for g in gaps]}})
        else:
            sections.append({"heading": "Evidence gaps", "body": "No review framework was given. A framework would enable evidence gaps."})
    if kind == "one_on_one":
        sections = one_on_one(profile, by_id, sts) + sections[:0] + [ledger_sec]
    if kind == "review_material":
        sections.insert(0, {"heading": f"Impact statements {date_from or 'all'} to {date_to or 'now'}", "bullets": [f"{s_.text} [{_cite(s_.evidence, by_id, 2)}]" for s_ in sts]})
    if kind == "interview_stories":
        st = stories(profile, by_id, terms, keep_internal)
        sections.insert(0, {"heading": "Interview stories", "sections": [{"heading": x["title"], "body": x["story"], "bullets": [f"Answers: {x['question']}", f"Evidence: {_cite(x['evidence'], by_id, 2)}"]} for x in st]})
    if run_notes or failed:
        sections.append({"heading": "What could not be done", "bullets": [*[f"{f['source']}: {f['error']}" for f in failed], *run_notes]})
    sections = [s_ for s_ in sections if s_.get("bullets") or s_.get("table") or s_.get("body") or s_.get("sections") or s_.get("facts")]
    corpus = {"title": title, "summary": f"**{s['prs_shipped']} PRs shipped**, {s['reviews_given']} reviews, {len(profile['invisible'])} pieces of invisible work, {len(profile['decisions'])} decisions. Every statement links to an item in the ledger.",
              "sections": sections, "sources": sources_for(items)}
    corpus["sections"][0:0] = [{"heading": "Key numbers", "facts": facts}] if kind in {"backfill", "profile"} else []
    # ledger must stay the first TABLE (CSV export reads the first table); key numbers have no table
    result = {"title": title, "mode": kind, "stats": s, "projects": len(profile["projects"]), "invisible_work": len(profile["invisible"]),
              "decisions": len(profile["decisions"]), "improvement_areas": [a["label"] for a in profile["improvements"]],
              "evidence_gaps": [{"expectation": g["expectation"], "status": g["status"]} for g in profile["gaps"]],
              "unavailable": list(dict.fromkeys(f["source"] for f in failed)),
              "unavailable_reasons": list({f["source"]: f.get("error", "") for f in failed}.items()), "notes": run_notes, "ledger_rows": len(rows)}
    return corpus, result
