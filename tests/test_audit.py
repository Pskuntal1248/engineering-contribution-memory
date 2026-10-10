"""Spec audit: typed requests, team changes, include_dms opt-in semantics."""
from datetime import date

from ask import parse_request, parse_team_changes
from guards import ToolLog, channels_to_read
from linking import split_by_team
from models import Item

CH = [{"id": "C1", "name": "general"}, {"id": "G1", "name": "secret", "is_private": True},
      {"id": "D1", "name": "", "user": "U77", "is_im": True}, {"id": "D2", "name": "", "user": "U88", "is_im": True}]
TODAY = date(2026, 10, 10)


def test_typed_requests_and_date_ranges():
    assert parse_request("weekly summary", TODAY)[0] == "weekly_summary"
    assert parse_request("1:1 points", TODAY)[0] == "one_on_one"
    assert parse_request("interview stories", TODAY)[0] == "interview_stories"
    assert parse_request("evidence gaps", TODAY)[0] == "evidence_gaps"
    assert parse_request("review material for April to September", TODAY) == ("review_material", "2026-04-01", "2026-09-30")
    assert parse_request("review material for November to March", TODAY) == ("review_material", "2025-11-01", "2026-03-31")
    assert parse_request("review material 2026-01-01 to 2026-03-31", TODAY) == ("review_material", "2026-01-01", "2026-03-31")
    assert parse_request("", TODAY) == ("", "", "")


def test_team_change_groups_by_team_with_date():
    ch = parse_team_changes("2026-07-01: Platform\n2026-03-01: Payments\nnot a line")
    assert ch == [("2026-03-01", "Payments"), ("2026-07-01", "Platform")]
    mk = lambda i, d: Item(id=i, source="github", type="pr", date=d, title="t", text="", author="me", meta={})
    out = split_by_team([mk("a", "2026-04-01T00:00:00Z"), mk("b", "2026-08-01T00:00:00Z")], ch)
    assert out == {"Payments": ["a"], "Platform": ["b"]}


def test_include_dms_no_never_reads_dms_even_when_named():
    log = ToolLog()
    picked, _ = channels_to_read(CH, ["general", "U77", "*dm*"], False, log)
    assert [c["id"] for c in picked] == ["C1"] and not log.dm_reads()


def test_include_dms_yes_reads_only_dms_the_user_names():
    picked, missing = channels_to_read(CH, ["general", "U77"], True)
    assert [c["id"] for c in picked] == ["C1", "D1"] and missing == []
    picked, _ = channels_to_read(CH, ["general"], True)
    assert [c["id"] for c in picked] == ["C1"]  # Yes alone opts in nothing
    picked, _ = channels_to_read(CH, ["general", "*dm*"], True)
    assert {c["id"] for c in picked} == {"C1", "D1", "D2"}


def test_private_channel_only_when_listed():
    assert [c["id"] for c in channels_to_read(CH, ["general"], False)[0]] == ["C1"]
    assert "G1" in [c["id"] for c in channels_to_read(CH, ["general", "secret"], False)[0]]


def test_markdown_copy_has_text_and_ledger(tmp_path):
    import outputs
    corpus = {"title": "T", "summary": "S", "sections": [{"heading": "Wins", "bullets": ["shipped X (PAY-1)"]},
                                                           {"heading": "Item ledger", "table": {"columns": ["source", "date"], "rows": [["github", "2026-09-01"]]}}]}
    md = outputs.to_markdown(corpus, with_tables=False)
    assert "shipped X (PAY-1)" in md and "github" not in md
    outputs.write_local_copy(corpus, str(tmp_path))
    assert "shipped X" in (tmp_path / "last_output.md").read_text() and (tmp_path / "last_ledger.csv").read_text().startswith("source,date")


def _thread(i, minute, text, user="Parth"):
    return Item(id=f"slack:G1:17900000{i}.0001", source="slack", type="thread", date=f"2026-09-18T10:{minute:02d}:00Z", title="#uc10-test thread", text=f"{user}: {text}",
                author=user, meta={"channel": "uc10-test", "channel_id": "G1", "messages": 1, "my_messages": 1, "participants": [user], "mine": True})


def test_consecutive_messages_on_one_incident_merge_but_other_topics_do_not():
    from pipeline import merge_threads
    a = _thread(1, 0, "settlement batch window mismatch, looks like UTC vs IST")
    b = _thread(2, 3, "traced the settlement batch window to a timezone bug, UTC vs IST")
    c = _thread(3, 6, "fix: batch window now uses IST, settlement matches")
    d = _thread(4, 30, "new joiner question about the retry backoff config, walked through it")
    out = merge_threads([a, b, c, d])
    assert len(out) == 2
    big = max(out, key=lambda i: i.meta["my_messages"])
    assert big.meta["my_messages"] == 3 and len(big.meta["merged_ids"]) == 2 and big.id == a.id


def test_one_on_one_is_first_person_linked_and_free_of_ids_and_trivial_wins():
    from outputs import one_on_one, no_ids, short, to_markdown
    mk = lambda i, t, **m: Item(id=i, source="github", type="pr", date="2026-09-01T00:00:00Z", title=t, url="u-" + i, keys=m.pop("keys", []), meta={"mine": True, "merged_flag": True, **m})
    init = mk("p0", "Initial commit")
    real = mk("p1", "PAY-1: idempotency key for payouts", keys=["PAY-1"])
    th = _thread(1, 0, "traced the batch window to a timezone bug")
    th.url = "u-th"
    prof = {"gaps": [], "projects": [], "decisions": [{"doc": "d", "title": "Design: Payout reconciliation job [UC10 TEST DATA] with a very long title that keeps going", "role": "contributed", "evidence": ["p1"], "summary": "decision is not stated"}],
            "invisible": [{"thread": th.id, "label": "problem_solved", "summary": "traced the batch window to a timezone bug", "evidence": [th.id], "co_contributors": ["U0BUNS1DT5E"]}],
            "improvements": [{"label": "missing tests", "count": 3, "item_ids": ["p1"]}]}
    out = one_on_one(prof, {i.id: i for i in (init, real, th)}, [])
    text = to_markdown({"title": "t", "summary": "", "sections": out})
    assert "I shipped this on 2026-09-01 (PAY-1)." in text and "[PAY-1: idempotency key for payouts](u-p1)" in text and "Initial commit" not in text
    assert "I debugged and fixed a problem in #uc10-test" in text and "no ticket raised" in text and "u-th" in text
    assert "U0BUNS1DT5E" not in text and "not stated" not in text and "the user" not in text.lower()
    assert "Working on" in text and "missing tests" in text
    assert no_ids("role contributed with U0BUNS1DT5E.") == "role contributed." and "U0BUNS1DT5E" not in no_ids("thanks U0BUNS1DT5E")
    s = short("Design: Payout reconciliation job [UC10 TEST DATA]", 30)
    assert s.count("[") == s.count("]") and not s.endswith("DAT")


def test_confluence_not_enabled_fails_gracefully_and_others_continue():
    import asyncio
    from adapters import fetch_confluence
    from guards import ToolLog

    async def raises(name, args):
        raise RuntimeError("Confluence is not enabled for this site (404)")

    out = {"docs": [], "failed": [], "capped": []}
    asyncio.run(fetch_confluence(raises, "2026-01-01T00:00:00Z", ToolLog(), out))
    assert out["docs"] == [] and "not enabled" in out["failed"][0]["error"] and out["failed"][0]["source"] == "docs:confluence"

    async def as_data(name, args):
        return {"successful": False, "error": "No Confluence app on this Atlassian site"}

    out2 = {"docs": [], "failed": [], "capped": []}
    asyncio.run(fetch_confluence(as_data, "2026-01-01T00:00:00Z", ToolLog(), out2))
    assert len(out2["failed"]) == 1 and "docs:confluence" == out2["failed"][0]["source"]


def test_docs_confluence_down_notion_and_drive_still_returned():
    from test_adapters import Gateway, fetch

    class NoConfluence(Gateway):
        async def __call__(self, name, args):
            if name.startswith("confluence."):
                raise RuntimeError("Confluence is not enabled on this site")
            return await super().__call__(name, args)

    out = fetch(NoConfluence())
    srcs = {d["_source"] for d in out["docs"]}
    assert srcs == {"notion", "drive"}
    assert [f for f in out["failed"] if f["source"] == "docs:confluence" and "not enabled" in f["error"]]


def _real(ts, text):
    from datetime import datetime, timezone
    d = datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return Item(id=f"slack:G1:{ts}", source="slack", type="thread", date=d, title="#uc10-test thread", text=f"Parth Singh: {text}", author="Parth Singh",
                meta={"channel": "uc10-test", "channel_id": "G1", "messages": 1, "my_messages": 1, "participants": ["Parth Singh"], "mine": True})


def test_flat_messages_at_the_users_real_timestamps_merge_per_topic():
    from pipeline import merge_threads
    flat = [
        _real("1791641890.36", "Settlement totals mismatch for the 18 Sep batch, looks like a timezone problem"),
        _real("1791641910.75", "Traced it: the batch job used UTC while the settlement cutoff is IST"),
        _real("1791641922.86", "Fixed the batch window config to use IST, settlement totals match now"),
        _real("1791641933.69", "Walked a new joiner through the null pointer in the retry worker"),
        _real("1791641945.33", "Mentored them on reading the stack trace for the null pointer"),
        _real("1791641959.69", "Paired on the null pointer fix, they will add a retry worker test"),
        _real("1791641969.96", "Pointed them to the retry worker docs for the null pointer follow-up"),
        _real("1791641991.63", "How do I run the payouts service locally? Start the compose stack and use the env file"),
    ]
    out = merge_threads(flat)
    sizes = sorted(i.meta["my_messages"] for i in out)
    assert sizes == [1, 3, 4], sizes


def test_clip_never_cuts_mid_word_and_first_person():
    from guards import clip, first_person
    t = clip("The deployment pipeline was fixed so that verification results now arrive before release and nothing ships blind to anyone", 60)
    assert t.endswith("...") and not t[:-3].endswith(" ") and t[:-3].split()[-1] in "The deployment pipeline was fixed so that verification results now arrive before release".split()
    assert clip("Short one. Second sentence runs on and on past the limit for sure here.", 40) == "Short one."
    assert first_person("The user fixed it. the user is on call; the user's fix") == "I fixed it. I am on call; my fix"


def test_thread_ts_is_read_from_the_permalink_when_the_search_hit_has_no_thread_ts():
    import asyncio
    from adapters import fetch_slack_search
    from guards import ToolLog
    seen = []

    async def call(tool, args):
        if tool == "slack.search_messages":
            return {"data": {"messages": {"matches": [{"ts": "200.1", "user": "U1", "channel": {"id": "G1"}, "permalink": "https://x.slack.com/archives/G1/p2001?thread_ts=100.5&cid=G1"}]}}}
        seen.append(args["ts"])
        return {"data": {"messages": [{"ts": "100.5", "user": "U1", "text": "root"}, {"ts": "200.1", "user": "U1", "text": "reply"}]}}
    out = {"failed": [], "capped": [], "slack": {"threads": []}}
    asyncio.run(fetch_slack_search(call, [{"id": "G1", "name": "uc10-test"}], "2026-09-01", ToolLog(), out))
    assert seen == ["100.5"] and len(out["slack"]["threads"]) == 1
