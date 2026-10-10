import normalize as N
from models import Item
import outputs as O


def test_slack_titles_are_complete_or_end_with_ellipsis():
    t = N.thread_title("I keep getting a null pointer in the payout handler when the retry queue is empty and nobody has looked at it yet")
    assert t.endswith("...") and not t[:-3].split()[-1].lower() in {"the", "in", "a"}
    t2 = N.thread_title("PAY-1 idempotency keys are live in staging. AUTH-2 got reopened after the rate limiter blocked health checks")
    assert t2 == "PAY-1 idempotency keys are live in staging. AUTH-2 got reopened after the rate limiter blocked health checks"
    t3 = N.thread_title("how do we run the payouts service locally with docker compose and the seeded database please tell me")
    assert not t3.rstrip(".").split()[-1] in {"the", "in", "with"}
    t4 = N.thread_title("Status update: everything in staging is fine and the new flag is on for all users and customers except for AUTH-2")
    assert not t4.rstrip(".").endswith("AUTH-2")


def test_lockfile_pr_without_file_list_is_generated_only():
    raw = {"number": 8, "title": "Update dependencies (lockfile only)", "user": {"login": "u"}, "created_at": "2026-09-20T10:00:00Z",
           "merged_at": "2026-09-20T11:00:00Z", "additions": 400, "deletions": 300, "html_url": "x", "body": ""}
    it = N.normalize_pr(raw, "o/r", "u")
    assert it.meta["generated_only"]
    raw2 = dict(raw, files=[{"path": "package-lock.json"}], title="Bump deps")
    assert N.normalize_pr(raw2, "o/r", "u").meta["generated_only"]
    raw3 = dict(raw, files=[{"filename": "package-lock.json"}, {"filename": "src/a.py", "additions": 3}], title="Bump deps")
    assert not N.normalize_pr(raw3, "o/r", "u").meta["generated_only"]


def test_pr_and_ticket_with_same_key_share_one_line():
    pr = Item(id="p", source="github", type="pr", date="2026-09-01", title="AUTH-1 Add session token refresh", url="u1", keys=["AUTH-1"], meta={"number": 6})
    tk = Item(id="t", source="jira", type="ticket", date="2026-08-30", title="AUTH-1: Add session token refresh [TEST DATA]", url="u2", keys=["AUTH-1"], meta={})
    lines = O._group_links([tk, pr])
    assert len(lines) == 1 and lines[0].startswith("AUTH-1 Add session token refresh: ") and "PR #6" in lines[0] and "ticket" in lines[0]


def test_ungrounded_redo_claim_dropped():
    from analysis import _grounded
    assert _grounded("I built login. It was to be redone.", "login work") == "I built login."
    assert _grounded("It was redone.", "the module was redone") == "It was redone."


def test_nothing_counted_is_invisible_on_the_page():
    from test_real_output import WORK, FOUR, profile, NAMES
    from outputs import corpus_for, to_markdown
    items = WORK + FOUR
    prof = profile(items)
    corpus, _ = corpus_for("backfill", prof, items, title="t", terms=set(), keep_internal=False, colleagues=[], names=NAMES,
                           date_from="", date_to="", run_notes=[], failed=[], quiet_line=None, new_ids=set())
    body = to_markdown(corpus, with_tables=False)
    for t in FOUR:
        assert t.url in body, t.url
    assert "invisible work (" in corpus["summary"] and "shows this" not in corpus["summary"]


def test_ellipsis_survives_page_cleanup_and_every_pr_pairs_with_its_ticket():
    assert O.no_placeholders("payouts for the 18 Sep batch...") == "payouts for the 18 Sep batch..."
    assert O.no_placeholders("done..") == "done."
    mk = lambda t, n, k, num=None: Item(id=f"{t}{n}", source="x", type=t, date=f"2026-09-0{n}", title=f"{k} Thing {n}", url=f"u{n}{t}", keys=[k, "AUTH-301"] if t == "pr" else [k], meta={"number": num} if num else {})
    items = [mk("pr", 1, "AUTH-1", 6), mk("pr", 2, "AUTH-2", 7), mk("ticket", 3, "AUTH-1"), mk("ticket", 4, "AUTH-2"), mk("ticket", 5, "AUTH-4")]
    lines = O._group_links(items)
    assert len(lines) == 3 and lines[0].startswith("AUTH-1 Thing 1: [PR #6]") and "ticket" in lines[0] and "AUTH-301" not in " ".join(lines)


def test_skills_count_distinct_work_not_pr_ticket_commit_separately():
    from analysis import _distinct_work
    mk = lambda i, t, ks, title: Item(id=i, source="x", type=t, date="2026-09-01", title=title, keys=ks, meta={})
    its = [mk("a", "pr", ["PAY-101", "PAY-1"], "PAY-1 Add idempotency key"), mk("b", "ticket", ["PAY-1"], "PAY-1: Add idempotency key"),
           mk("c", "commit", ["PAY-101"], "PAY-101 add key"), mk("d", "pr", ["PAY-2"], "PAY-2 Retry")]
    assert len(_distinct_work(its)) == 2


def test_confluence_pages_list_is_used_when_cql_and_identity_are_refused():
    import asyncio
    from adapters import fetch_confluence
    from guards import ToolLog

    async def call(name, args):
        if name in ("confluence.cql_search", "confluence.get_current_user"):
            raise RuntimeError("401 Unauthorized: scope does not match")
        if name == "confluence.get_pages":
            return {"data": {"results": [
                {"id": "65930", "title": "Payout design", "authorId": "acc1", "createdAt": "2026-09-20T10:00:00Z", "version": {"createdAt": "2026-09-21T10:00:00Z", "authorId": "acc1"},
                 "body": {"storage": {"value": "<p>Design of retries</p>"}}, "_links": {"webui": "/spaces/UTD/pages/65930"}},
                {"id": "65931", "title": "Plan", "authorId": "acc1", "createdAt": "2026-09-22T10:00:00Z", "version": {"createdAt": "2026-09-22T10:00:00Z", "authorId": "acc1"},
                 "body": {"storage": {"value": "<p>Confidential plan</p>"}}}], "_links": {"base": "https://x.atlassian.net/wiki"}}}
        if name == "confluence.get_labels_for_page":
            raise RuntimeError("403")
        raise RuntimeError("no")
    out = {"docs": [], "failed": [], "capped": []}
    asyncio.run(fetch_confluence(call, "2026-01-01T00:00:00Z", ToolLog(), out))
    assert len(out["docs"]) == 2 and not out["failed"], out
    assert out["docs"][0]["url"].startswith("https://x.atlassian.net/wiki/spaces/UTD")
