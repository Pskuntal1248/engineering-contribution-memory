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
