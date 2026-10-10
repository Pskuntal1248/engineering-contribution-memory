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
