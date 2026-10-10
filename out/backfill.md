# Engineering profile - Pskuntal1248

5 PRs shipped, 0 reviews, 3 pieces of invisible work (1 listed under Problems solved), 3 decisions. Every item below links to its source.

## Projects

**Product search query parsing and ranking (2026-10-10)**

I added query parsing, but the ranking change was reverted because its weights were wrong for short queries.

- Goal: I built search features to handle quoted phrases and exclusions and improve result relevance.
- Role: built

Evidence:
- SRCH-1 Add query parser: [PR #3](https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/3), [ticket](https://pskuntal2005.atlassian.net/browse/SRCH-1)
- SRCH-2 Add result ranking: [PR #5](https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/5), [PR #11](https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/11), [ticket](https://pskuntal2005.atlassian.net/browse/SRCH-2)

**Payout reliability (2026-10-10)**

I added idempotency keys, exponential-backoff retries, and a nightly reconciliation job.

- Goal: I built payout safeguards and recovery processes to prevent duplicate payouts, retry failed bank calls, and reconcile settlement totals.
- Role: built
- Slack, 2026-10-10: PAY-1 idempotency keys are live in staging. AUTH-2 reopened after the rate limiter blocked health checks.

Evidence:
- PAY-1 Add idempotency key to payouts: [PR #1](https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/1), [ticket](https://pskuntal2005.atlassian.net/browse/PAY-1)
- PAY-2 Retry failed payouts with backoff: [PR #2](https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/2), [ticket](https://pskuntal2005.atlassian.net/browse/PAY-2)
- PAY-3 Add payout reconciliation job: [PR #4](https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/4), [ticket](https://pskuntal2005.atlassian.net/browse/PAY-3)
- [PAY-4: Investigate payout dashboard latency (no PR)](https://pskuntal2005.atlassian.net/browse/PAY-4)
- [PAY-1 idempotency keys are live in staging. AUTH-2 reopened after the rate limiter...](https://the-glider.slack.com/archives/C0C7U7L87MM/p1791642029600679)
- [Confidential: Payout incident review](https://pskuntal2005.atlassian.net/wiki/spaces/UTD/pages/229387/Confidential+Payout+incident+review+UC10+TEST+DATA) [CONFIDENTIAL source - check before sharing]

**Session token refresh and login rate limiting (2026-10-10)**

I added session token refresh; the login limiter was reverted because it locked out shared office IPs.

- Goal: I built session token refresh to reduce mid-checkout expirations and a per-user login limiter to slow password guessing.
- Role: built

Evidence:
- AUTH-1 Add session token refresh: [PR #6](https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/6), [ticket](https://pskuntal2005.atlassian.net/browse/AUTH-1)
- AUTH-2 Add login rate limiter: [PR #7](https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/7), [PR #10](https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/10), [ticket](https://pskuntal2005.atlassian.net/browse/AUTH-2)


## Problems solved

**payouts for the 18 Sep batch don't match settlement totals, anyone seen this?**

I traced the payout mismatch to a UTC-versus-IST day-window issue, fixed the window builder, and reran the batch so totals matched.

- 2026-10-10, no ticket raised

[Source: Slack conversation](https://the-glider.slack.com/archives/C0C7U7L87MM/p1791641890360289)


## Decisions

**Design: Search ranking approach [UC10 TEST DATA]**

My role: led. The search ranking design chooses BM25 with title-weighted scoring, a recency boost, and rollout behind a feature flag.

[Source: doc](https://pskuntal2005.atlassian.net/wiki/spaces/UTD/pages/229377/Design+Search+ranking+approach+UC10+TEST+DATA)

**Design: Payout reconciliation job [UC10 TEST DATA]**

My role: led. The accepted design is to run payout reconciliation nightly as a queue-backed job rather than a cron script, enabling idempotent retries.

[Source: doc](https://app.notion.com/p/Design-Payout-reconciliation-job-UC10-TEST-DATA-3f5d2067fa9c80e1ba57f18448a1a6a5)

**docs: ADR-001 move webhooks to a queue**

My role: led. Proposes: move outbound webhooks to the platform team shared queue.

[Source: PR](https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/9)


## Invisible work

**I keep getting a null pointer in the retry worker, not sure how to debug it.**

I guided debugging by logging the job payload and tracing how the retry path rebuilt it, which revealed a missing merchant_id.

- 2026-10-10, no ticket raised

[Source: Slack conversation](https://the-glider.slack.com/archives/C0C7U7L87MM/p1791641945332839)

**how do we run the payouts service locally with the sandbox gateway?**

I explained how to run the payouts service locally with the sandbox gateway and said I would add the instructions to the README.

- 2026-10-10, no ticket raised

[Source: Slack conversation](https://the-glider.slack.com/archives/C0C7U7L87MM/p1791641991634509)

Unticketed debugging and fixes (1) are listed once, under Problems solved.


## Skills

**payments & idempotency**

4 pieces of work show this.

For example:
- [PAY-1 Add idempotency key to payouts](https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/1)
- [PAY-2 Retry failed payouts with backoff](https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/2)

**technical design**

2 pieces of work show this.

For example:
- [docs: ADR-001 move webhooks to a queue](https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/9)
- [Design: Payout reconciliation job](https://app.notion.com/p/Design-Payout-reconciliation-job-UC10-TEST-DATA-3f5d2067fa9c80e1ba57f18448a1a6a5)

**async messaging (queues)**

1 piece of work shows this.

[docs: ADR-001 move webhooks to a queue](https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/9)

**debugging & incident response**

1 piece of work shows this.

[Confidential: Payout incident review](https://pskuntal2005.atlassian.net/wiki/spaces/UTD/pages/229387/Confidential+Payout+incident+review+UC10+TEST+DATA) [CONFIDENTIAL source - check before sharing]


## Improvement areas

None found: no feedback pattern repeated 3 or more times.


## Evidence gaps

No review framework was given, so evidence gaps are skipped. A framework would enable them.


## What could not be done

- capped: confluence: read through the pages list (the CQL search was refused); only the 100 most recently edited pages were checked; Confluence authorship could not be confirmed (identity call refused), so pages with real content were kept
- Rows not counted (23): merge commit or commit already counted through its PR (11); sample, template or space overview page generated by the tool, not your work (6); space home page (overview), not a page you wrote (3); commit already counted through its PR (same title or same files, inside the PR's dates) (1); page has no title (1); profile page, not work (1).
- no review framework given: evidence gaps skipped; a framework would enable them

## Appendix: item ledger (also the CSV download)

38 items: 12 commits, 11 PRs, 8 tickets, 4 Slack conversations, 3 docs. A PR, its commits and its ticket are separate rows for the same change. Reverts, reverted PRs, lockfile-only PRs and trivial commits are listed but not counted.

| source | date | project | type | title | url | counted |
| --- | --- | --- | --- | --- | --- | --- |
| github | 2026-10-10 | (unlinked) | commit | Initial commit | https://github.com/Pskuntal1248/uc10-synthetic-test-data/commit/445b034e08b4e8c618ff2a87523b0b69b61b7863 | listed, not counted (trivial commit) |
| github | 2026-10-10 | (unlinked) | commit | Update README.md | https://github.com/Pskuntal1248/uc10-synthetic-test-data/commit/cbe3ec011cac0e6c2b13142284ae563fbaff7e60 | listed, not counted (trivial commit) |
| github | 2026-10-10 | Payout reliability | commit | PAY-101 Add idempotency key to payouts | https://github.com/Pskuntal1248/uc10-synthetic-test-data/commit/6b97aa7763403f9616c5d15cd693cb175348d64e | counted |
| github | 2026-10-10 | Payout reliability | pr | PAY-1 Add idempotency key to payouts | https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/1 | shipped |
| github | 2026-10-10 | Payout reliability | commit | PAY-102 Retry failed payouts with backoff | https://github.com/Pskuntal1248/uc10-synthetic-test-data/commit/4fc3e11abbe1c479d0da982126469b7058b2217e | counted |
| github | 2026-10-10 | Payout reliability | pr | PAY-2 Retry failed payouts with backoff | https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/2 | shipped |
| github | 2026-10-10 | Payout reliability | commit | PAY-103 Add payout reconciliation job | https://github.com/Pskuntal1248/uc10-synthetic-test-data/commit/50f927486625a3cec9ac74760cdc0730e4be6b65 | counted |
| github | 2026-10-10 | Product search query parsing and ranking | commit | SRCH-201 Add query parser | https://github.com/Pskuntal1248/uc10-synthetic-test-data/commit/d323ccf3c6fdaf9bfe6b1c066455db01d1289c7d | counted |
| github | 2026-10-10 | Product search query parsing and ranking | pr | SRCH-1 Add query parser | https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/3 | shipped |
| github | 2026-10-10 | Payout reliability | pr | PAY-3 Add payout reconciliation job | https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/4 | shipped |
| github | 2026-10-10 | Product search query parsing and ranking | commit | SRCH-202 Add result ranking | https://github.com/Pskuntal1248/uc10-synthetic-test-data/commit/9c4ef9fbee82e9d37eeeca39ab5370e433fa4a8a | counted |
| github | 2026-10-10 | Session token refresh and login rate limiting | commit | AUTH-301 Add session token refresh | https://github.com/Pskuntal1248/uc10-synthetic-test-data/commit/3689e5055fed092a863f5558f945762fbee1574a | counted |
| github | 2026-10-10 | Product search query parsing and ranking | pr | SRCH-2 Add result ranking | https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/5 | reverted (not shipped) |
| github | 2026-10-10 | Session token refresh and login rate limiting | pr | AUTH-1 Add session token refresh | https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/6 | shipped |
| github | 2026-10-10 | Session token refresh and login rate limiting | commit | AUTH-302 Add login rate limiter | https://github.com/Pskuntal1248/uc10-synthetic-test-data/commit/bea8ca9a5801a468f65284da9b385847868dc854 | counted |
| github | 2026-10-10 | Session token refresh and login rate limiting | pr | AUTH-2 Add login rate limiter | https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/7 | reverted (not shipped) |
| github | 2026-10-10 | (unlinked) | commit | Update print statement from 'Hello' to 'Goodbye' | https://github.com/Pskuntal1248/uc10-synthetic-test-data/commit/69bdcfa4f4caab7550ce4d0db7ad6ac1291dab89 | listed, not counted (trivial commit) |
| github | 2026-10-10 | (unlinked) | pr | Update dependencies (lockfile only) | https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/8 | generated or lockfile changes only (not counted) |
| github | 2026-10-10 | Payout reliability | pr | docs: ADR-001 move webhooks to a queue | https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/9 | documentation or proposal only (not counted as shipped) |
| github | 2026-10-10 | Session token refresh and login rate limiting | commit | Revert "AUTH-302 Add login rate limiter" | https://github.com/Pskuntal1248/uc10-synthetic-test-data/commit/753a87d62a26045317f123a7a6458f0513a3665f | revert (not counted) |
| github | 2026-10-10 | Session token refresh and login rate limiting | pr | Revert "AUTH-2 Add login rate limiter" | https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/10 | revert (not counted) |
| github | 2026-10-10 | Product search query parsing and ranking | commit | Revert "SRCH-202 Add result ranking" | https://github.com/Pskuntal1248/uc10-synthetic-test-data/commit/451ec4b729664ebca51b40c38538814aa3b0d4ed | revert (not counted) |
| github | 2026-10-10 | Product search query parsing and ranking | pr | Revert "SRCH-2 Add result ranking" | https://github.com/Pskuntal1248/uc10-synthetic-test-data/pull/11 | revert (not counted) |
| jira | 2026-10-10 | Payout reliability | ticket | PAY-1: Add idempotency key to payouts [TEST DATA] | https://pskuntal2005.atlassian.net/browse/PAY-1 |  |
| jira | 2026-10-10 | Payout reliability | ticket | PAY-2: Retry failed payouts with backoff [TEST DATA] | https://pskuntal2005.atlassian.net/browse/PAY-2 |  |
| jira | 2026-10-10 | Payout reliability | ticket | PAY-3: Add payout reconciliation job [TEST DATA] | https://pskuntal2005.atlassian.net/browse/PAY-3 |  |
| jira | 2026-10-10 | Payout reliability | ticket | PAY-4: Investigate payout dashboard latency (no PR) [TEST DATA] | https://pskuntal2005.atlassian.net/browse/PAY-4 |  |
| jira | 2026-10-10 | Product search query parsing and ranking | ticket | SRCH-1: Add query parser [TEST DATA] | https://pskuntal2005.atlassian.net/browse/SRCH-1 |  |
| jira | 2026-10-10 | Product search query parsing and ranking | ticket | SRCH-2: Add result ranking [TEST DATA] | https://pskuntal2005.atlassian.net/browse/SRCH-2 |  |
| jira | 2026-10-10 | Session token refresh and login rate limiting | ticket | AUTH-1: Add session token refresh [TEST DATA] | https://pskuntal2005.atlassian.net/browse/AUTH-1 |  |
| jira | 2026-10-10 | Session token refresh and login rate limiting | ticket | AUTH-2: Add login rate limiter [TEST DATA] | https://pskuntal2005.atlassian.net/browse/AUTH-2 |  |
| doc | 2026-10-10 | Payout reliability | doc | Design: Payout reconciliation job [UC10 TEST DATA] | https://app.notion.com/p/Design-Payout-reconciliation-job-UC10-TEST-DATA-3f5d2067fa9c80e1ba57f18448a1a6a5 |  |
| slack | 2026-10-10 | Payout reliability | thread | payouts for the 18 Sep batch don't match settlement totals, anyone seen this? | https://the-glider.slack.com/archives/C0C7U7L87MM/p1791641890360289 |  |
| slack | 2026-10-10 | (unlinked) | thread | I keep getting a null pointer in the retry worker, not sure how to debug it. | https://the-glider.slack.com/archives/C0C7U7L87MM/p1791641945332839 |  |
| slack | 2026-10-10 | (unlinked) | thread | how do we run the payouts service locally with the sandbox gateway? | https://the-glider.slack.com/archives/C0C7U7L87MM/p1791641991634509 |  |
| slack | 2026-10-10 | Payout reliability | thread | PAY-1 idempotency keys are live in staging. AUTH-2 reopened after the rate limiter blocked... | https://the-glider.slack.com/archives/C0C7U7L87MM/p1791642029600679 |  |
| doc | 2026-10-10 | Product search query parsing and ranking | doc | Design: Search ranking approach [UC10 TEST DATA] | https://pskuntal2005.atlassian.net/wiki/spaces/UTD/pages/229377/Design+Search+ranking+approach+UC10+TEST+DATA |  |
| doc | 2026-10-10 | Payout reliability | doc | Confidential: Payout incident review [UC10 TEST DATA] | https://pskuntal2005.atlassian.net/wiki/spaces/UTD/pages/229387/Confidential+Payout+incident+review+UC10+TEST+DATA |  |
