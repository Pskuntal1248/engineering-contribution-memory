"""Career-ladder parsing and a deterministic evidence mapper. The model may map
better; its picks are validated against real ids and counted here either way."""
from __future__ import annotations

import re
from dataclasses import dataclass

from linking import tokens
from metrics import status_for
from models import Item

_LEVEL = re.compile(r"^\s*(?:#+\s*|\*\*)?(?:level\s*:?\s*)?((?:senior\s+)?(?:software\s+)?(?:engineer|sde|swe)\s*(?:i{1,3}|iv|v|\d)?|staff\s+engineer|principal\s+engineer|se\s*(?:i{1,3}|iv|v|\d))\b", re.I)


@dataclass
class Expectation:
    id: str
    level: str  # "current" | "next"
    text: str


def parse_framework(text: str, current_level: str, next_level: str | None = None) -> list[Expectation]:
    """Bullet or numbered lines under a heading that names the current or next level.
    Headings are matched by level name; with no match every bullet counts as 'next'."""
    cur = re.sub(r"\W+", "", current_level.split(",")[0].lower())
    nxt = re.sub(r"\W+", "", (next_level or "").lower())
    section, out = "next", []
    bullets = 0
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(#+|\*\*)?\s*(.*?)\s*(?:\*\*)?:?$", line)
        bullet = re.match(r"^(?:[-*•]|\d+[.)])\s+(.*)$", line)
        if bullet:
            bullets += 1
            t = bullet.group(1).strip().rstrip(".")
            out.append(Expectation(f"E{len(out)+1}", section, t))
        else:
            h = re.sub(r"\W+", "", line.lower())
            if cur and cur in h:
                section = "current"
            elif (nxt and nxt in h) or "nextlevel" in h or "promotion" in h:
                section = "next"
            elif _LEVEL.match(line):
                section = "next" if section == "current" else section
    return out


_TOPICS = [  # (expectation regex, predicate over an item). Deterministic baseline; the model mapping replaces it when used.
    (r"review", lambda i: i.type == "review" and i.meta.get("mine") and len(i.text) > 12),
    (r"mentor|unblock|coach|onboard", lambda i: i.type == "thread" and i.meta.get("label") == "mentoring"),
    (r"design|document|adr|rfc", lambda i: i.type == "doc" and i.meta.get("mine")),
    (r"cross.?team|stakeholder.*decision|leads? a .*decision", lambda i: i.type in {"doc", "thread"} and bool(re.search(r"cross.?team|multiple teams|org.?wide|across teams", i.text, re.I)) and i.meta.get("mine")),
    (r"incident|debug|reliab|on.?call", lambda i: i.type == "thread" and i.meta.get("label") == "problem_solved"),
    (r"test|quality|automation", lambda i: i.type == "pr" and i.meta.get("mine") and bool(re.search(r"\btests?\b|\bqa\b|automation", i.title + " " + i.text, re.I))),
    (r"deliver|feature|independent|ship", lambda i: i.type == "pr" and i.meta.get("mine") and i.meta.get("merged_flag") and not i.meta.get("reverted")),
    (r"communicat|status|risk", lambda i: i.type == "thread" and i.meta.get("root_is_mine")),
]


def map_evidence(exp: Expectation, items: list[Item], per: int = 8) -> list[str]:
    for pat, pred in _TOPICS:
        if re.search(pat, exp.text, re.I):
            return [i.id for i in sorted((i for i in items if pred(i)), key=lambda i: (i.date, i.id))][:per]
    return _map_lexical(exp, items, per)


def _map_lexical(exp: Expectation, items: list[Item], per: int = 8) -> list[str]:
    """Deterministic baseline: items whose text shares enough distinctive words with the expectation."""
    et = set(tokens(exp.text))
    if not et:
        return []
    scored = []
    for it in items:
        if not it.meta.get("mine") and it.type != "review":
            continue
        overlap = len(et & set(tokens(it.title + " " + it.text[:800])))
        need = 2 if len(et) >= 3 else 1
        if overlap >= need:
            scored.append((-overlap, it.date, it.id))
    return [i for _, _, i in sorted(scored)[:per]]


def statuses(expectations: list[Expectation], evidence: dict[str, list[str]], known: set[str]) -> list[dict]:
    """backed (3+ distinct valid items), thin (1-2), missing (none). Invalid ids never count."""
    rows = []
    for e in expectations:
        ids = [i for i in dict.fromkeys(evidence.get(e.id, [])) if i in known]
        rows.append({"id": e.id, "level": e.level, "expectation": e.text, "status": status_for(ids), "evidence": ids})
    return rows
