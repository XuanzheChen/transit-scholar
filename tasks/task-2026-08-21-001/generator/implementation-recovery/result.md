STATUS: completed
NEXT: evaluator
BLOCKING: false

### Implementation

- Added strict Pydantic JSON Schema, `auto|json_schema|json_object` modes, narrow capability fallback, one bounded correction, and redaction in `llm.py`.
- Preserved shared-client wiring across extraction, semantic verification, targeted recheck, and one-field smoke.
- Removed stale unreachable code and added explicit capability-code fallback coverage.
- Updated smoke, public exports, focused tests, and development documentation.
- Preserved all pre-existing changes outside the execution contract; `.env`, Gold, fixtures, Package E, plugins, L2S1, databases, and workflow artifacts were not edited.

Changed package files: `src/transit_scholar/layer2/schema_extraction/{__init__,api,engine,errors,llm,semantic}.py`, `scripts/l2s2_runtime_smoke.py`, six focused `tests/test_l2s2_*.py` files, and `doc/20260814-L2S2-Schema提取与验证开发情况说明.md`.

### Validation

- Focused required command: `218 passed in 7.54s`.
- Literal `python -m pytest tests/test_l2s2_*.py -q`: exit 1, because PowerShell passes the wildcard literally; no tests collected.
- PowerShell-expanded equivalent: `629 passed in 23.35s`.
- Literal network-blocked wildcard command: same shell-glob collection failure.
- Expanded `TRANSIT_SCHOLAR_BLOCK_NETWORK=1` suite: `629 passed in 24.10s`.
- `git diff --check`: clean aside from existing LF→CRLF warnings.

### Guarantees

- Deterministic suites use fakes/MockTransport; the blocked suite made no real network requests.
- API keys, Authorization/Bearer values, and complete base URLs are redacted from correction context, errors, persisted records, and smoke output.
- Documentation now records the 85-warning breakdown, Package E null metrics, updated `218/629` counts, and keeps the real-smoke blocker open.

The real smoke was intentionally not run. It requires valid runtime provider/model/key/base URL configuration, `TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK=1`, existing `transit-001` retrieval data, and process-local `TRANSIT_SCHOLAR_BLOCK_NETWORK=0`; success requires exit 0, a valid `SemanticVerdict`, and `final_success=true`.