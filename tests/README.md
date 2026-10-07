# Tests

Run commands from the repository root with uv and Python 3.14+. See the
[project README](../README.md) for setup and the [release notes](../CHANGELOG.md)
for capabilities and known limitations.

## Offline tests

```bash
uv sync --locked
uv run pytest
uv run pytest --cov=google_analytics_mcp_server --cov-report=term-missing
```

The default run deselects live tests and blocks ADC resolution and network access.
Tests use the real discovery client with a recording HTTP fake. They cover native
and raw routing, request schemas, all feature flags, guards, output, retries and
sandbox recovery. Coverage includes branches and enforces a 90% combined threshold;
offline coverage does not establish live API eligibility or service semantics.

To select tests or inspect failures:

| Task | Command |
|---|---|
| One file | `uv run pytest tests/test_policy.py` |
| Match test names | `uv run pytest -k read_only` |
| List tests without executing | `uv run pytest --collect-only -q` |
| Show each case and stop on the first failure | `uv run pytest -v -x` |

### Installed wheel

```bash
uv build
uv run --isolated --no-project --with dist/*.whl python -I scripts/check_wheel.py
```

Run this after packaging changes. It checks imports, packaged schemas/examples,
the console entry point and tool registration, with ADC/network blocked. Imports
from the source checkout are rejected.

## Read-only live tests

Use existing ADC with the required [OAuth scopes](../README.md#3-authorize-access)
and permissions for an authorized test property. Tests do not log in or modify the
ADC file. Supply a numeric property ID or `properties/PROPERTY_ID`, not a `G-...`
measurement ID; `GA_MCP_TEST_PROPERTY` can replace `--property`.

```bash
uv run pytest tests/e2e --live --property=YOUR_TEST_PROPERTY_ID
```

This runs only read-only cases in strict read-only mode. It checks navigation,
metadata, report types, access/history reads, errors, pagination, a narrowed token
and a real MCP SDK stdio client. It consumes real API quota; live tests never run
in CI. Missing pagination data, an unobserved page edge, unavailable user ADC or
a token that Google does not narrow may produce explicit skips. API failures fail
the run.

To select only standard reports:

```bash
uv run pytest tests/e2e/test_smoke.py -k standard_report \
  --live --property=YOUR_TEST_PROPERTY_ID -v
```

## Sandbox writes

Use a dedicated sandbox. Authorize the exact targets, principal, operations and
any retained access before execution; reviewing a preview alone is not permission.
Select the test files and cases below so only reviewed operations run. Keep
credentials, target/recipient details, inventories and journals in ignored `tmp/`.
Use new output paths for each plan and journal, preserving earlier evidence.

### Property fixtures and jobs

This workflow requires an existing audience and a direct Administrator binding
for the supplied ADC principal. First capture a read-only inventory and generate
an offline preview:

```bash
mkdir -p tmp
uv run python -m tests.e2e.inventory \
  --property=YOUR_TEST_PROPERTY_ID --principal=YOUR_ADC_EMAIL \
  --output=tmp/live-inventory.json
uv run python -m tests.e2e.sandbox \
  --inventory=tmp/live-inventory.json --output=tmp/live-plan.json
```

The preview exercises MCP tools and the discovery client with ADC/network blocked.
Names ending in `/0` are placeholders replaced by names returned from live creates.
Review the target, payloads and retained jobs, then execute:

```bash
uv run pytest tests/e2e/test_sandbox.py --live --live-write \
  --property=YOUR_TEST_PROPERTY_ID \
  --sandbox-plan=tmp/live-plan.json --sandbox-journal=tmp/live-journal.json
```

The cases cover temporary beta/alpha streams, URI patches/clears, an alpha event
rule, property-binding updates preserving the original roles, and all three
report/audience job create/get/query flows.

### Additional alpha resources and Chat

Reuse the property inventory to generate a separate preview:

```bash
uv run python -m tests.e2e.advanced \
  --inventory=tmp/live-inventory.json --output=tmp/advanced-plan.json
uv run pytest tests/e2e/test_advanced.py --live --live-write \
  --property=YOUR_TEST_PROPERTY_ID \
  --sandbox-plan=tmp/advanced-plan.json --sandbox-journal=tmp/advanced-journal.json \
  -k additional_alpha_resource_lifecycle
```

Review the preview before the pytest command. The selected cases create, update
and delete temporary annotations and calculated metrics. Annotation cleanup accepts
HTTP 403 only when a complete parent listing independently proves absence;
calculated-metric cleanup requires HTTP 404.

Chat requires `analytics.chatbot.read` and explicit session-test authorization.
For a separately reviewed Chat run, generate a fresh advanced plan/journal and
replace the selector with `-k chat`. HTTP 403 is a failed prerequisite. Sessions
have no delete method; record returned IDs. The earlier ambiguous Chat creation
is not authorization to retry it.

### Property administrator restoration

This case temporarily removes/replaces an existing direct property binding.
It requires explicit approval of the principal, property and restoration procedure,
plus independent account-level Administrator access. The runner snapshots the
binding, restores the original roles in `finally` and verifies both levels;
account permissions are not changed.

```bash
uv run python -m tests.e2e.access \
  --inventory=tmp/live-inventory.json --output=tmp/access-plan.json
uv run pytest tests/e2e/test_access_lifecycle.py --live --live-write \
  --property=YOUR_TEST_PROPERTY_ID \
  --sandbox-plan=tmp/access-plan.json --sandbox-journal=tmp/access-journal.json
```

Review the generated preview before execution. It covers single/batch create,
role replacement and delete, including single empty-role deletion.

### Account/property access sandbox

Select an account and its property. Capture both levels without changing access:

```bash
uv run python -m tests.e2e.account_access \
  --account=YOUR_TEST_ACCOUNT_ID --property=YOUR_TEST_PROPERTY_ID \
  --principal=YOUR_ADC_EMAIL --output=tmp/account-access-inventory.json
```

For single/batch account updates preserving the original roles, generate and
review the preview before the pytest command:

```bash
uv run python -m tests.e2e.account_access \
  --inventory=tmp/account-access-inventory.json \
  --output=tmp/account-access-unchanged-plan.json
uv run pytest tests/e2e/test_account_access.py \
  -k updates_preserve_original_roles --live --live-write \
  --account=YOUR_TEST_ACCOUNT_ID --property=YOUR_TEST_PROPERTY_ID \
  --sandbox-plan=tmp/account-access-unchanged-plan.json \
  --sandbox-journal=tmp/account-access-unchanged-journal.json
```

Full lifecycles require an approved test email different from the ADC operator,
with no existing direct binding at either level. Review the generated preview
before running single/batch create, role replacement, delete and empty-role deletion:

```bash
uv run python -m tests.e2e.account_access \
  --inventory=tmp/account-access-inventory.json \
  --test-user=APPROVED_TEST_EMAIL --output=tmp/account-access-temporary-plan.json
uv run pytest tests/e2e/test_account_access.py \
  -k temporary_access_lifecycle --live --live-write \
  --account=YOUR_TEST_ACCOUNT_ID --property=YOUR_TEST_PROPERTY_ID \
  --sandbox-plan=tmp/account-access-temporary-plan.json \
  --sandbox-journal=tmp/account-access-temporary-journal.json
```

Original administrator bindings are preserved and complete list readbacks verify
both levels. Cleanup targets only the selected recipient's temporary bindings.
Batch tests use one recipient, so they do not verify multi-user transaction semantics.

Only when retaining access is explicitly authorized, generate a new preview with
`--retain-for-review` and select both lifecycles plus the review-grant case:

```bash
uv run python -m tests.e2e.account_access \
  --inventory=tmp/account-access-inventory.json --test-user=APPROVED_TEST_EMAIL \
  --retain-for-review --output=tmp/account-access-review-plan.json
uv run pytest tests/e2e/test_account_access.py \
  -k 'temporary_access_lifecycle or retain_viewer_for_user_review' \
  --live --live-write --account=YOUR_TEST_ACCOUNT_ID \
  --property=YOUR_TEST_PROPERTY_ID --maxfail=1 \
  --sandbox-plan=tmp/account-access-review-plan.json \
  --sandbox-journal=tmp/account-access-review-journal.json
```

After both lifecycles pass and their fixtures are removed, this case leaves one
Viewer grant per level. Failed or ambiguous final grants are recorded without
replay or automatic removal.

### Cleanup and failures

- Journals record requests before execution, returned identifiers and cleanup.
  Cleanup deletes only resources created by the case and verifies absence.
- Never replay a started case or an ambiguous write, Chat or job creation. Inspect
  the journal and unresolved outcomes before considering further live work.
- After a job timeout, poll its recorded name with `ga_get` and query when active;
  do not recreate it. The three job collections have no delete endpoint.
- Do not exhaust live quotas or generalize observed patch/clear/array behavior
  across methods. Historical results do not authorize other targets or operations.

## Recorded verification

These are sanitized local observations, not a new run or evidence of hosted CI
success or publication. Detailed inventories and journals remain outside Git.

| Evidence | Recorded result |
|---|---|
| Offline, 2026-10-06 | 3,965 passed; 39 live cases deselected; 91.36% combined statement/branch coverage. Ruff, formatting and strict mypy passed. |
| Selected live workflows, 2026-10-03 to 2026-10-06 | Reports, access/history, errors, pagination, all three job flows, temporary configuration and access lifecycles passed. Temporary fixtures were removed and original administrator roles preserved. |
| Chat, 2026-10-06 | Consent verified; creation returned HTTP 500 without a session ID. Outcome unknown, continuation untested, request not replayed. |

Successful Chat sessions, Analytics 360 and account provisioning/redirect remain
unverified. Quota exhaustion and empty-page-plus-token behavior are synthetic only;
recipient-login effective access and multi-user batch semantics were not verified.
Live patch/clear/array evidence applies only to the tested methods.
