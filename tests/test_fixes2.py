import normalize as N
from models import Item
import outputs as O


def test_slack_titles_are_complete_or_end_with_ellipsis():
    t = N.thread_title("I keep getting a null pointer in the payout handler when the retry queue is empty and nobody has looked at it yet")
    assert t.endswith("...") and not t[:-3].split()[-1].lower() in {"the", "in", "a"}
    t2 = N.thread_title("PAY-1 idempotency keys are live in staging. AUTH-2 got reopened after the rate limiter blocked health checks")
    assert t2 == "PAY-1 idempotency keys are live in staging."
    t3 = N.thread_title("how do we run the payouts service locally with docker compose and the seeded database please tell me")
    assert not t3.rstrip(".").split()[-1] in {"the", "in", "with"}
    t4 = N.thread_title("Status update: everything in staging is fine and the new flag is on for all users except for AUTH-2")
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
    pr = Item(id="p", source="github", type="pr", date="2026-09-01", title="Add session token refresh", url="u1", keys=["AUTH-1"], meta={"number": 6})
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
