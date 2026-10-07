# Repository guidance

Python 3.14+, uv, google-api-python-client and MCP SDK 2.x. Local stdio server for
Google Analytics Admin/Data APIs, documented as an alpha release.

## Workflow

- [README.md](README.md): installation and capabilities; [CHANGELOG.md](CHANGELOG.md):
  changes and limitations; [tests/README.md](tests/README.md): test procedures and
  sanitized evidence. Keep public docs aligned with verified capabilities and limits.
- Use a `codex/` branch for production code changes. Commit, push, tag, publish,
  change MCP host configuration or mutate real Analytics data only on user instruction.
- Keep credentials and sensitive test data outside version control.
- Use single-quoted Python strings, type hints and Google-style docstrings. Write
  comments and project docs in English.
- Reserve server stdout for MCP messages; send diagnostics to stderr.
- Keep tool descriptions and server instructions below 2,048 UTF-8 bytes each.

## Commands

```bash
uv sync --locked
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
uv run pytest --cov=google_analytics_mcp_server --cov-report=term-missing
uv build
uv run --isolated --no-project --with dist/*.whl python -I scripts/check_wheel.py
uv run google-analytics-mcp-server
uv run python scripts/update_discovery.py --check
```

The server command waits for an MCP client over stdio. Discovery maintenance uses
public endpoints; `--write` updates only snapshots and hashes.

## Architecture

- `catalog.py`, `capabilities.py`, `names.py`: pinned discovery schemas and routing.
- `contracts.py`, `policy.py`: validation, defaults, masks, budgets and guards.
- `client.py`, `auth.py`: HTTP execution, transport locking and lazy ADC.
- `projection.py`, `render.py`: lossless values, complete pages and output limits.
- `registry.py`, `server.py`, `tools/`, `examples/`: MCP tools, profiles and schema help.

## Implementation invariants

- Every API request goes through `client.execute`. Resolve the exact version before
  field validation or defaults and keep that version fixed throughout execution.
- `roles=[]` deletes an AccessBinding. Confirm all single/batch access writes;
  generic create/update/delete must refuse them.
- Native updates use writable leaf masks; broad masks require raw dispatch and
  confirmation. Parent masks and array replacement have method-specific semantics.
- No token/unknown-quota retries or replay of writes, chat or job creation after
  ambiguous failures. Report-job polling releases the shared transport lock.
- Return each upstream list page intact with its matching continuation token.
- Cohorts omit dateRanges. Realtime/funnel have no offset paging; pivots page
  separately. Access reports use returnEntityQuota; account-level requests use false.
- Keep full API fields available. Declare raw-only global query controls and value
  restrictions separately from method ownership.
- Retain mutation completion state and identifiers even if output is large or
  formatting fails.
- Keep startup, tool listing and schema help offline and independent of ADC.

## Verification

- Default tests block ADC/network access; CI runs only static checks and offline tests.
  Use the real discovery client with a recording HTTP fake and separate endpoint,
  field, profile and output assertions. Cover all feature-flag combinations and versioned
  native/raw routes; validate examples against every advertised version.
- Run the clean-wheel check after packaging changes. Live verification must use the
  current checkout or a freshly built wheel; verify the registered version in MCP hosts.
- Live tests require authorized targets and operations. Dry-run production scripts
  first; sandbox writes require `--live-write`, an offline preview, a dedicated
  sandbox and a durable journal. Never replay a started sandbox case; resume timed-out
  jobs by polling their recorded names.
- Authorize access targets, principals, operations and retained grants explicitly.
  Preserve original administrators. Changes to an existing property administrator
  binding require independent account Administrator access, a direct-binding snapshot,
  restoration of original roles in `finally` and verification of both levels.
- Use synthetic quota-exhaustion fixtures and method-specific patch/clear/array assertions.
- Revalidate the scoped google-auth stdin workaround when upgrading that dependency.
