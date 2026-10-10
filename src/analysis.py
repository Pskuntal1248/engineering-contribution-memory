"""Judgement steps. Each has a deterministic baseline (no model) and an optional model pass.
The model returns typed objects with evidence ids; code drops anything not backed by a real
item, caps roles, removes colleague assessments and counts every number."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from guards import Budget, BudgetExceeded, Statement, cap_role, co_contributors, drop_colleague_assessments, keep_cited
from linking import tokens
from metrics import shipped
from models import Item

# ----------------------------------------------------------------- schemas (typed model outputs)
Label = Literal["problem_solved", "decision", "help_given", "mentoring", "none"]


class ThreadVerdict(BaseModel):
    thread_id: str = Field(description="the id given in the input, copied exactly")
    label: Label
    summary: str = Field(description="one sentence from the thread text only: what was wrong and what the user did")
    user_role: Literal["led", "built", "contributed", "reviewed", "supported"] = "supported"


class ThreadVerdicts(BaseModel):
    verdicts: list[ThreadVerdict]


class ProjectCard(BaseModel):
    project_id: str
    name: str
    goal: str = Field(description="from the evidence only")
    outcome: str = Field(description="from the evidence only; say 'not stated' if unknown")
    role: Literal["led", "built", "contributed", "reviewed", "supported"]
    evidence: list[str] = Field(description="item ids copied exactly from the input")


class ProjectCards(BaseModel):
    projects: list[ProjectCard]


class LLM(Protocol):
    async def structured(self, schema: type[BaseModel], system: str, payload: dict[str, Any], budget: Budget | None = None) -> BaseModel: ...


# ---------------------------------------------------------------------------- baselines
_PROBLEM = re.compile(r"\b(traced|root cause|fixed|fix(?:ed)?|patched|cleared|pinned|moved it to|found (?:the|that)|took the page|debug\w*|workaround|added a lock)\b", re.I)
_HELP = re.compile(r"\b(walked through|explained|shared the|showed|here is the|use the|advised|paired|how (?:it )?works?|replay)\b", re.I)
_MENTOR = re.compile(r"\b(mentored|paired|walked (?:him|her|them) through|new joiner|onboard\w*|taught|showed the token)\b", re.I)
_DECISION = re.compile(r"\b(we decided|decision|chose|chosen|going with|agreed to|approach:)\b", re.I)


def has_strong_link(it: Item, links: dict[str, list[str]]) -> bool:
    return bool(it.keys) or any(not (r.startswith("close in time") or r.startswith("same topic")) for r in links.get(it.id, []))


def my_text(it: Item, display_names: set[str]) -> str:
    lines = [l.split(": ", 1) for l in it.text.splitlines() if ": " in l]
    mine = [b for a, b in lines if a.strip() in display_names]
    return " ".join(mine) or it.text


def baseline_thread(it: Item, display_names: set[str]) -> tuple[Label, str]:
    t = my_text(it, display_names)
    if _DECISION.search(t):
        label: Label = "decision"
    elif _MENTOR.search(t):
        label = "mentoring"
    elif _PROBLEM.search(t):
        label = "problem_solved"
    elif _HELP.search(t):
        label = "help_given"
    else:
        label = "none"
    first = re.sub(r"\s+", " ", t).strip()
    return label, first[:220]


def find_invisible(items: list[Item], links: dict[str, list[str]], display_names: set[str],
                   verdicts: dict[str, ThreadVerdict] | None = None) -> list[dict[str, Any]]:
    """Slack threads the user took part in with no ticket, PR or doc link: the work nobody recorded."""
    out = []
    for it in items:
        if it.type != "thread" or not it.meta.get("mine") or has_strong_link(it, links):
            continue
        v = (verdicts or {}).get(it.id)
        label, summary = (v.label, v.summary) if v else baseline_thread(it, display_names)
        if label == "none":
            continue
        # a thread where the user only posted a short ack is not work
        if it.meta.get("my_messages", 0) < 1 or len(my_text(it, display_names).split()) < 9:
            continue
        out.append({"thread": it.id, "label": label, "summary": summary, "evidence": [it.id], "date": it.date,
                    "co_contributors": [p for p in it.meta.get("participants", []) if p not in display_names]})
    return sorted(out, key=lambda d: (d["date"], d["thread"]))


def find_decisions(items: list[Item], display_names: set[str], username: str) -> list[dict[str, Any]]:
    """Design docs / ADRs. Role: author of the doc = led; editor or reviewer only = contributed."""
    out = []
    for it in items:
        if it.type != "doc" or not (it.meta.get("mine")):
            continue
        labs = " ".join(it.meta.get("labels", [])).lower()
        if not (re.search(r"\b(adr|rfc|design)\b", labs + " " + it.title.lower()) and _DECISION.search(it.text)):
            continue
        author_is_me = it.author in display_names or it.author.lower() == username.lower()
        out.append({"doc": it.id, "title": it.title, "role": "led" if author_is_me else "contributed", "evidence": [it.id],
                    "summary": re.sub(r"\s+", " ", it.text)[:220], "confidential": bool(it.meta.get("confidential")),
                    "co_contributors": [] if author_is_me else [it.author]})
    return sorted(out, key=lambda d: d["doc"])


def baseline_project_card(pid: str, ids: list[str], by_id: dict[str, Item], username: str) -> dict[str, Any]:
    ev = [by_id[i] for i in ids]
    tickets = [e for e in ev if e.type == "ticket"]
    words: Counter[str] = Counter()
    for e in ev:
        words.update(tokens(e.title))
    top = [w for w, _ in sorted(words.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
    prs = [e for e in ev if e.type == "pr" and e.meta.get("mine")]
    shipped_n = sum(1 for e in prs if shipped(e))
    role = cap_role("built" if prs else "contributed", ev, username)
    dates = sorted(e.date for e in ev)
    return {"project_id": pid, "name": " ".join(top).title() or pid, "goal": "not stated in the sources (model summary needed)",
            "outcome": f"{shipped_n} PRs shipped, {sum(1 for t in tickets if t.meta.get('status','').lower() in {'done','closed','resolved'})} tickets done",
            "role": role, "first": dates[0][:10], "last": dates[-1][:10], "evidence": sorted(ids, key=lambda i: (by_id[i].date, i))[:60]}


SKILL_LEXICON = {
    "payments & idempotency": r"idempot|payout|upi|ledger|settlement",
    "async messaging (queues)": r"queue|webhook|consumer|dead letter",
    "frontend & accessibility": r"checkout|accessib|contrast|design system|component|storybook",
    "debugging & incident response": r"incident|pager|root cause|traced|debug",
    "code review": r"review",
    "mentoring": r"mentor|paired|new joiner|onboard",
    "technical design": r"adr|design doc|rfc|decision",
}


def map_skills(items: list[Item]) -> list[dict[str, Any]]:
    out = []
    for skill, pat in sorted(SKILL_LEXICON.items()):
        rx = re.compile(pat, re.I)
        ids = [i.id for i in items if (i.meta.get("mine") or i.type == "review") and rx.search(i.title + " " + i.text[:600])]
        if ids:
            out.append({"skill": skill, "count": len(ids), "evidence": sorted(ids)[:6]})
    return sorted(out, key=lambda d: (-d["count"], d["skill"]))


# --------------------------------------------------------------------------- model passes
async def classify_threads_with_model(llm: LLM | None, budget: Budget, threads: list[Item], names: set[str],
                                      batch: int = 15, max_batches: int = 40) -> tuple[dict[str, ThreadVerdict], list[str]]:
    """One call per `batch` candidate threads. Returns verdicts keyed by id, plus notes on anything skipped.
    Only ids we sent are accepted back."""
    notes: list[str] = []
    verdicts: dict[str, ThreadVerdict] = {}
    if llm is None:
        return verdicts, ["model not used for thread labels (baseline rules)"]
    sys = ("You label Slack threads for an engineer's work record. For each thread say whether the user solved a problem, "
           "took part in a decision, gave help or mentored, or none. Use ONLY the thread text. Never judge other people. "
           "Copy thread_id exactly.")
    sent = threads[: batch * max_batches]
    if len(threads) > len(sent):
        notes.append(f"{len(threads) - len(sent)} threads not sent to the model (call cap); labelled by rules")
    for i in range(0, len(sent), batch):
        chunk = sent[i : i + batch]
        payload = {"user_names": sorted(names), "threads": [{"thread_id": t.id, "text": t.text[:700]} for t in chunk]}
        try:
            res = await llm.structured(ThreadVerdicts, sys, payload, budget)
        except BudgetExceeded as e:
            notes.append(str(e))
            break
        except Exception as e:  # noqa: BLE001
            notes.append(f"model call failed: {str(e)[:120]}")
            continue
        ok = {t.id for t in chunk}
        for v in res.verdicts:  # type: ignore[attr-defined]
            if v.thread_id in ok:
                verdicts[v.thread_id] = v
    return verdicts, notes


async def project_cards_with_model(llm: LLM | None, budget: Budget, base: list[dict[str, Any]], by_id: dict[str, Item],
                                   username: str, per_call: int = 5) -> tuple[list[dict[str, Any]], list[str]]:
    """Names, goals, outcomes and roles. Evidence ids are validated; role is capped by code."""
    notes: list[str] = []
    if llm is None:
        return base, ["project names and goals from rules (model not used)"]
    sys = ("Name each project and state its goal and outcome using ONLY the evidence given. Say 'not stated' when unknown. "
           "Role: led only if the user drove the work; reviewing is 'reviewed'. Copy ids exactly.")
    out = {b["project_id"]: dict(b) for b in base}
    for i in range(0, len(base), per_call):
        chunk = base[i : i + per_call]
        payload = {"projects": [{"project_id": b["project_id"], "evidence": [{"id": e, "title": by_id[e].title, "text": by_id[e].text[:220], "type": by_id[e].type} for e in b["evidence"][:25]]} for b in chunk]}
        try:
            res = await llm.structured(ProjectCards, sys, payload, budget)
        except BudgetExceeded as e:
            notes.append(str(e)); break
        except Exception as e:  # noqa: BLE001
            notes.append(f"model call failed: {str(e)[:120]}"); continue
        for c in res.projects:  # type: ignore[attr-defined]
            if c.project_id not in out:
                continue
            ev = [e for e in c.evidence if e in by_id] or out[c.project_id]["evidence"]
            out[c.project_id].update(name=c.name[:80], goal=c.goal[:240], outcome=c.outcome[:240], role=cap_role(c.role, [by_id[e] for e in ev], username))
    return list(out.values()), notes
