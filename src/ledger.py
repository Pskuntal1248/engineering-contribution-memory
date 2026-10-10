"""The item ledger: one row per item. Columns source,date,project,type come first
(the CSV the platform exports from the first corpus table), then id/title/url/counting."""
from __future__ import annotations

import csv
import io

from models import Item
from metrics import shipped

COLUMNS = ["source", "date", "project", "type", "title", "url", "counted"]


def counted_label(it: Item) -> str:
    if it.type == "pr":
        if it.meta.get("is_revert"):
            return "revert (not counted)"
        if it.meta.get("reverted"):
            return "reverted (not shipped)"
        return "shipped" if shipped(it) else "open or closed unmerged"
    if it.type == "commit":
        if it.meta.get("is_revert"):
            return "revert (not counted)"
        if it.meta.get("reverted"):
            return "reverted (not counted)"
        return "counted" if it.meta.get("mine") else ""
    return ""


def rows(items: list[Item], project_of: dict[str, str], cap: int | None = None) -> tuple[list[list[str]], bool]:
    ordered = sorted(items, key=lambda i: (i.date, i.id))
    truncated = cap is not None and len(ordered) > cap
    if truncated:
        ordered = ordered[-cap:]  # keep the newest
    return [[i.source, i.date[:10], project_of.get(i.id, "(unlinked)"), i.type, i.title[:90], i.url, counted_label(i)] for i in ordered], truncated


def to_csv(table_rows: list[list[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    w.writerows(table_rows)
    return buf.getvalue()
