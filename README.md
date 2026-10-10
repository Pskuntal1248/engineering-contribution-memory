# Engineering Contribution Memory (UC-10)

A Gravity monitor agent (gravity-cli 0.5.0, plain LangGraph). Read-only: it never writes to GitHub, Jira, Slack or docs.

Reads your last 6 months of work, links PRs, tickets, discussions and docs into projects, finds work that never made it into a ticket, and keeps an evidence-linked profile. Code computes every number; the model (optional) only labels and words, and its output is validated against real item ids.

- `src/normalize.py`, `linking.py`, `metrics.py`, `guards.py`, `ledger.py`, `weekly.py`: pure logic, unit tested
- `src/sources.py`: source adapters (real gateway tools, fixtures for tests); failed or missing sources are named in the result
- `src/store.py`: profile storage behind one interface (native `state.memory` now, `get_store` when confirmed)
- `src/agent.py`: LangGraph pipeline, `async graph()`
- `tests/`: our own fixture built from the spec text (not Gravity's samples)

Run tests: `uv venv --python 3.12 && uv pip install -r requirements.txt pytest && pytest`

Status: work in progress, private, not submitted to Gravity.

## On-demand requests (checked against the Gravity 0.5.0 guide)
This agent is `kind: monitor`. The guide offers follow-up runs with the user's words (`inputs["message"]`) only for `kind: interactive`, so a monitor does not get them. On-demand answers (what did I do on project X, year summary, review-pack, promotion evidence) are chosen with the `request` dropdown plus `period_from` / `period_to` on a manual run. They are answered from the saved profile with no source reads. Free-text follow-ups are not supported; this is a deliberate choice, not an oversight.

## Required inputs and example run
Required (as in the UC-10 inputs table): `github_username`, `repos_or_org`, `jira_site`, `jira_projects`, `slack_channels`, `role_and_level`, `timezone` (defaults to Asia/Kolkata). Optional: `include_dms` (No), `docs_source`, `backfill_months` (6), `review_framework`. Jira and docs tools are not in Gravity's catalog yet, so those sources are reported as unavailable, not silently skipped.
```
gravity run --input github_username='USER' --input repos_or_org='owner/repo' --input jira_site='https://example.atlassian.net' --input jira_projects='ABC' --input slack_channels='#general' --input role_and_level='Software Engineer' --input display_name='Full Name' --input backfill_months='6'
gravity run --input github_username='USER' --input repos_or_org='owner/repo' --input jira_site='https://example.atlassian.net' --input jira_projects='ABC' --input slack_channels='#general' --input role_and_level='Software Engineer' --input request='one_on_one'
```
Slack is read only for the channels named in `slack_channels`. Commits by the user are read too (via github.list_commits and github.get_commit), so repos without PRs still show work. Commits already counted through a PR, merge commits and bot commits are dropped; a commit that a later "This reverts commit" points at is listed but not counted. The second command answers from saved memory (`.gravity/memory.json`) even if the first run found nothing.
