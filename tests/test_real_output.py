"""The shape of the user's real run (synthetic repo PAY/SRCH/AUTH, 4 Slack topics, init commits), pinned as tests."""
import asyncio

from eprofile import build
from guards import budget_for
from models import Item
from outputs import corpus_for
from pipeline import merge_threads

ME = "Pskuntal1248"
NAMES = {ME, "Parth Singh"}
R = "Pskuntal1248/uc10-synthetic-test-data"


def pr(n, key, title, body="", merged=True, ts="2026-10-10T08:00:00Z"):
    return Item(id=f"gh:pr:{R}#{n}", source="github", type="pr", date=ts, title=f"{key} {title}", url=f"https://github.com/{R}/pull/{n}", text=body, author=ME, keys=[key],
                meta={"mine": True, "repo": R, "number": n, "merged_flag": merged, "state": "closed"})


def ticket(key, title, ts="2026-10-10T07:00:00Z"):
    return Item(id=f"jira:{key}", source="jira", type="ticket", date=ts, title=f"{key} {title}", url=f"https://x.atlassian.net/browse/{key}", text=title, author="Parth Singh", keys=[key],
                meta={"mine": True, "status": "Done"})


def commit(sha, title):
    return Item(id=f"gh:commit:{R}@{sha}", source="github", type="commit", date="2026-10-10T06:00:00Z", title=title, url=f"https://github.com/{R}/commit/{sha}", author=ME,
                meta={"mine": True, "repo": R})


def thread(i, minute, text, keys=()):
    return Item(id=f"slack:G1:17900{i}.000100", source="slack", type="thread", date=f"2026-10-10T09:{minute:02d}:00Z", title="#uc10-test thread", url=f"https://s/p{i}",
                text=f"Parth Singh: {text}\nParth Singh: more detail in a reply so the thread has two messages", author="Parth Singh", keys=list(keys),
                meta={"channel": "uc10-test", "channel_id": "G1", "messages": 2, "my_messages": 2, "participants": ["Parth Singh"], "mine": True})


FOUR = [
    thread(1, 0, "Settlement totals mismatch for the 18 Sep batch. Traced it: the batch job used UTC while the cutoff is IST, so I fixed the window config"),
    thread(2, 4, "Walked a new joiner through the null pointer in the retry worker, mentored them on reading the stack and pairing on the fix"),
    thread(3, 7, "How do I run the payouts service locally? Explained: start the compose stack, set the env file, then use the replay script here"),
    thread(4, 9, "Status: PAY-1 idempotency is shipped and AUTH-2 was reopened after the token refresh bug, tracking both", keys=("PAY-1", "AUTH-2")),
]
WORK = [
    ticket("PAY-1", "Add idempotency key to payouts"), ticket("PAY-2", "Retry failed payouts with backoff"), ticket("PAY-3", "Add payout reconciliation job"),
    ticket("SRCH-1", "Add query parser"), ticket("SRCH-2", "Add result ranking"), ticket("AUTH-1", "Add session token refresh"), ticket("AUTH-2", "Fix token refresh race"),
    pr(1, "PAY-1", "Add idempotency key to payouts"), pr(2, "PAY-2", "Retry failed payouts with backoff"),
    pr(4, "PAY-3", "Add payout reconciliation job", body="Reconciles settlements nightly. Needed debugging of the ledger mismatch and a component for the report."),
    pr(3, "SRCH-1", "Add query parser"), pr(5, "SRCH-2", "Add result ranking"), pr(6, "AUTH-1", "Add session token refresh", body="Adds a refresh component for the session client."),
    commit("445b034", "Initial commit"), commit("cbe3ec0", "Update README.md"),
]


def profile(items):
    return asyncio.run(build(items, username=ME, names=NAMES, llm=None, budget=budget_for("backfill"), framework="", role_and_level="Software Engineer", prior=None))


def test_four_topics_posted_minutes_apart_stay_four_items():
    out = merge_threads([Item(**t.__dict__) for t in FOUR])
    assert len(out) == 4


def test_invisible_work_keeps_the_three_unticketed_threads_and_not_the_status_thread():
    prof = profile(WORK + FOUR)
    labels = sorted(v["label"] for v in prof["invisible"])
    assert prof["invisible"] and len(prof["invisible"]) == 3, labels
    assert {"problem_solved", "mentoring", "help_given"} == set(labels)
    assert all("AUTH-2" not in v["summary"] for v in prof["invisible"])


def test_projects_follow_pay_srch_auth_with_readable_names_and_no_init_project():
    prof = profile(WORK + FOUR)
    names = [p["name"] for p in prof["projects"]]
    assert len(names) == 3, names
    assert sorted(n.rsplit("(", 1)[1].rstrip(")") for n in names) == ["AUTH", "PAY", "SRCH"]
    assert all(n[0].isupper() and "Srch" not in n and "Pay " not in n for n in names)
    assert sorted(names) == ["Payouts (PAY)", "Query parser (SRCH)", "Session token refresh (AUTH)"], names
    ev = {p["name"]: set(p["evidence"]) for p in prof["projects"]}
    pay = next(v for k, v in ev.items() if k.endswith("(PAY)"))
    assert f"gh:pr:{R}#1" in pay and f"gh:pr:{R}#6" not in pay and "jira:AUTH-1" not in pay
    assert not any("commit" in e for p in prof["projects"] for e in p["evidence"])  # init/README commits are not a project


def test_skills_need_real_evidence():
    prof = profile(WORK + FOUR)
    by = {s["skill"]: s["evidence"] for s in prof["skills"]}
    assert not any("pull/4" in e or e.endswith("#4") for e in by.get("debugging & incident response", []) if "gh:pr" in e)
    assert f"gh:pr:{R}#6" not in by.get("frontend & accessibility", [])


def test_profile_page_has_all_seven_sections_even_when_empty_and_the_ledger_is_last():
    prof = profile([ticket("PAY-1", "Add idempotency key to payouts")])
    corpus, _ = corpus_for("backfill", prof, [ticket("PAY-1", "Add idempotency key to payouts")], title="t", terms=set(), keep_internal=False, colleagues=[], names=NAMES,
                           date_from="", date_to="", run_notes=[], failed=[], quiet_line=None, new_ids=set())
    heads = [s["heading"] for s in corpus["sections"]]
    for h in ("Projects", "Problems solved", "Decisions", "Invisible work", "Skills", "Improvement areas", "Evidence gaps"):
        assert h in heads
    assert not any("Key numbers" in h for h in heads) and heads[-1].startswith("Appendix: item ledger")
    tables = [s for s in corpus["sections"] if s.get("table")]
    assert len(tables) == 1 and tables[0] is corpus["sections"][-1]  # the CSV export reads the first (only) table
    empty = next(s for s in corpus["sections"] if s["heading"] == "Invisible work")
    assert empty["body"].startswith("None found")


def test_placeholder_phrases_and_double_periods_never_show():
    from outputs import no_placeholders
    assert no_placeholders("An initial commit and a README update are recorded; further outcome not stated..") == "An initial commit and a README update are recorded."
