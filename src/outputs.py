"""Corpus + result builders. Every number comes from the profile (computed in code); prose is templated
from facts, optionally reworded by the model with evidence ids that code validates."""
from __future__ import annotations

import re
from typing import Any

import ledger
from guards import FLAG, Statement, drop_colleague_assessments, flag_confidential, internal_terms, keep_cited, predicts_promotion, scrub
from metrics import shipped
from models import Item

LEDGER_CAP = 6000


_SLACK_ID = re.compile(r"\b[UW](?=[A-Z0-9]*\d)[A-Z0-9]{8,11}\b")
_TRIVIAL = re.compile(r"^\s*(initial commit|first commit|init(ial(ize|ise)?)?\b|repository init|repo init|bootstrap|scaffold|add (readme|license|gitignore)|update readme|chore: ?init)", re.I)
_PLACEHOLDER = re.compile(r"\bnot stated\b|model summary needed|\(unlinked\)", re.I)


def short(title: str, n: int = 70) -> str:
    """A title for display: trailing [tags] dropped, cut at a word boundary, never mid-bracket."""
    t = re.sub(r"\s*\[[^\]]*\]\s*$", "", (title or "").strip()) or (title or "").strip()
    if len(t) <= n:
        return t
    cut = t[:n].rsplit(" ", 1)[0]
    if cut.count("[") > cut.count("]"):
        cut = cut[: cut.rfind("[")].rstrip()
    return cut.rstrip(" ,;:-") + "..."


def no_ids(text: str) -> str:
    """Raw Slack user IDs are never shown."""
    t = re.sub(r"\b(with|and|by|from)\s+" + _SLACK_ID.pattern + r"(?:,\s*" + _SLACK_ID.pattern + r")*", "", text)
    t = _SLACK_ID.sub("a teammate", t)
    return re.sub(r"\s{2,}", " ", t).replace(" .", ".").replace(" ;", ";")


def _clean_obj(x: Any) -> Any:
    if isinstance(x, str):
        return no_ids(x)
    if isinstance(x, list):
        return [_clean_obj(v) for v in x]
    if isinstance(x, dict):
        return {k: (v if k in {"url", "id"} else _clean_obj(v)) for k, v in x.items()}
    return x


def _link(it: Item | None) -> str:
    return it.url if it and it.url else (it.id if it else "")


_PRI = {"pr": 0, "ticket": 1, "doc": 2, "commit": 3, "review": 4, "thread": 5}


def _cite(ids: list[str], by_id: dict[str, Item], n: int = 3) -> str:
    out = []
    ids = sorted((i for i in ids if i in by_id), key=lambda i: (_PRI.get(by_id[i].type, 9), by_id[i].date, i))
    for i in ids[:n]:
        it = by_id.get(i)
        if it:
            out.append(f"{short(it.title, 60)} ({it.url or it.id})")
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
                tm = ""
                if len(p.get("teams") or []) > 1:
                    tm = " Teams: " + ", ".join(f"{t['team']}{' (from ' + t['since'] + ')' if t['since'] else ''}" for t in p["teams"]) + "."
                outcome = "" if _PLACEHOLDER.search(p["outcome"]) else f": {p['outcome']}"
                sts.append(Statement(f"{p['name']}{outcome}; my role: {p['role']}{co}.{tm}", p["evidence"][:6], p["role"]))
    for v in profile["invisible"]:
        if within(v["evidence"]):
            sts.append(Statement(f"Not in any ticket ({v['label'].replace('_',' ')}): {v['summary']}", v["evidence"], "supported"))
    for d in profile["decisions"]:
        if within(d["evidence"]):
            sts.append(Statement(f"Decision ({d['role']}): {short(d['title'], 90)}." + ("" if _PLACEHOLDER.search(d["summary"]) else f" {d['summary']}"), d["evidence"], d["role"]))
    for a in profile["improvements"]:
        sts.append(Statement(f"Recurring feedback, {a['count']} instances: {a['label']}.", a["item_ids"][:8]))
    conf = set(profile.get("confidential", []))
    sts = [flag_confidential(s, conf) for s in sts]
    sts, _ = keep_cited(sts, set(by_id))
    sts, _ = drop_colleague_assessments(sts, colleagues)
    sts = [s for s in sts if not predicts_promotion(s.text)]
    return sts


def _first_person_win(it: Item) -> str:
    keys = [k for k in it.keys if k][:2]
    return f"I shipped {short(it.title, 80)}" + (f" ({', '.join(keys)})" if keys else "")


_VERB = {"problem_solved": "I debugged and fixed a problem", "help_given": "I helped a teammate", "mentoring": "I mentored a teammate", "decision": "I drove a technical choice"}


def one_on_one(profile: dict[str, Any], by_id: dict[str, Item], sts: list[Statement]) -> list[dict[str, Any]]:
    """First-person talking points. Every bullet ends with a link. Wins are shipped PRs (initial/trivial commits excluded)."""
    mine = [i for i in by_id.values() if i.meta.get("mine")]
    prs = [i for i in mine if i.type == "pr" and shipped(i) and not _TRIVIAL.search(i.title)]
    prs.sort(key=lambda i: (-(int((i.meta.get("size") or {}).get("lines", 0)) > 0), -len(i.keys), i.date), reverse=False)
    prs = sorted(prs, key=lambda i: (-len(i.keys), i.date))[:4]
    wins = [f"{_first_person_win(i)} ({i.url or i.id})" for i in prs]
    if not wins:  # no shipped PRs: finished tickets, still real work
        done = [i for i in mine if i.type == "ticket" and str(i.meta.get("status", "")).lower() in {"done", "closed", "resolved"}][:3]
        wins = [f"I closed {short(i.title, 80)} ({i.url or i.id})" for i in done]
    unseen = []
    for v in profile["invisible"][:5]:
        it = by_id.get(v["evidence"][0]) if v["evidence"] else None
        if not it:
            continue
        where = f" in #{it.meta.get('channel')}" if it.meta.get("channel") else ""
        co = [p for p in v.get("co_contributors", []) if not _SLACK_ID.fullmatch(p)][:2]
        summ = re.sub(r"\s+", " ", v["summary"]).strip()
        unseen.append(f"{_VERB.get(v['label'], 'I helped')}{where} on {it.date[:10]}{' with ' + ', '.join(co) if co else ''}: {summ[:200]}. No ticket was raised. ({it.url or it.id})")
    decs = []
    for d in profile["decisions"][:3]:
        it = by_id.get(d["evidence"][0])
        role = "I led" if d["role"] == "led" else "I contributed to"
        decs.append(f"{role} {short(d['title'], 80)}; I'd like a call on whether we take it forward. ({(it.url if it else '') or d['evidence'][0]})")
    blockers = [f"Ticket reopened {i.meta.get('reopen_count')}x: {short(i.title, 80)} ({i.url or i.id})" for i in sorted(mine, key=lambda x: x.date)
                if i.type == "ticket" and i.meta.get("reopen_count", 0) > 0][:3]
    working = [f"{short(i.title, 80)}, PR still open ({i.url or i.id})" for i in sorted(mine, key=lambda x: x.date)
               if i.type == "pr" and i.meta.get("state") == "open" and not i.meta.get("is_revert")][:3]
    working += [f"{short(i.title, 80)}, ticket in progress ({i.url or i.id})" for i in sorted(mine, key=lambda x: x.date)
                if i.type == "ticket" and str(i.meta.get("status", "")).lower() in {"in progress", "in review", "in development"}][:3]
    for a in profile["improvements"][:2]:
        first = by_id.get(a["item_ids"][0]) if a["item_ids"] else None
        working.append(f"Recurring review feedback, {a['count']} instances: {a['label']}. ({(first.url if first else '') or a['item_ids'][0]})")
    gaps = [g for g in profile["gaps"] if g["status"] != "backed" and g["level"] == "next"]
    out = [{"heading": "Wins", "bullets": wins}, {"heading": "You may not have seen", "bullets": unseen}, {"heading": "Blockers", "bullets": blockers},
           {"heading": "Decisions needing input", "bullets": decs}, {"heading": "Working on", "bullets": working}]
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
        pool.append((f"Delivering {p['name']}", "; ".join(x for x in (p["outcome"], "" if _PLACEHOLDER.search(p["goal"]) else f"goal: {p['goal']}") if x and not _PLACEHOLDER.search(x)), p["evidence"][:6], "Tell me about a project you delivered end to end."))
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
    corpus = _clean_obj(corpus)
    # ledger must stay the first TABLE (CSV export reads the first table); key numbers have no table
    result = {"title": title, "mode": kind, "stats": s, "projects": len(profile["projects"]), "invisible_work": len(profile["invisible"]),
              "decisions": len(profile["decisions"]), "improvement_areas": [a["label"] for a in profile["improvements"]],
              "evidence_gaps": [{"expectation": g["expectation"], "status": g["status"]} for g in profile["gaps"]],
              "unavailable": list(dict.fromkeys(f["source"] for f in failed)),
              "unavailable_reasons": list({f["source"]: f.get("error", "") for f in failed}.items()), "notes": run_notes, "ledger_rows": len(rows)}
    return corpus, result


def to_markdown(corpus: dict[str, Any], with_tables: bool = True, level: int = 2) -> str:
    """The page as plain markdown, so a local run can be read without the platform."""
    lines: list[str] = []
    if level == 2:
        lines += [f"# {corpus.get('title', '')}", "", str(corpus.get("summary", "")), ""]
    for sec in corpus.get("sections", []):
        lines += [f"{'#' * level} {sec.get('heading', '')}", ""]
        if sec.get("body"):
            lines += [str(sec["body"]), ""]
        for f in sec.get("facts", []) or []:
            lines.append(f"- {f['label']}: {f['value']} (source: {f.get('source', '')})")
        for b in sec.get("bullets", []) or []:
            lines.append(f"- {b}")
        if sec.get("table") and with_tables:
            t = sec["table"]
            lines += ["| " + " | ".join(map(str, t["columns"])) + " |", "|" + " --- |" * len(t["columns"])]
            lines += ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in t["rows"]]
        if sec.get("sections"):
            lines.append(to_markdown({"sections": sec["sections"]}, with_tables, level + 1))
        lines.append("")
    return "\n".join(lines)


def write_local_copy(corpus: dict[str, Any], directory: str = ".gravity") -> None:
    """Local convenience only: lets `gravity run` output be read from disk. Never touches a source; failures are ignored."""
    try:
        import csv
        import os
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "last_output.md"), "w", encoding="utf-8") as f:
            f.write(to_markdown(corpus))
        first = next((s["table"] for s in corpus.get("sections", []) if s.get("table")), None)
        if first:
            with open(os.path.join(directory, "last_ledger.csv"), "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(first["columns"])
                w.writerows(first["rows"])
    except Exception:  # noqa: BLE001
        pass
