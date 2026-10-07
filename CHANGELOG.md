# Changelog

## 0.1.0

Initial alpha release for local stdio MCP clients. Requires Python 3.14+.

### Added

- Google Analytics Admin/Data alpha and beta API support with bundled schemas,
  26 default tools, optional Chat/raw dispatch and a strict read-only mode.
- Resource and access management, reports, metadata, change history and asynchronous
  report/audience jobs.
- ADC authentication, confirmation guards, writable leaf masks and bounded read retries.
- Markdown/JSON output with lossless metrics, intact pagination and retained
  mutation identifiers.
- Offline schema help and examples, discovery maintenance, tests and CI/release workflows.

### Verification

Local static checks, offline tests, packaging checks and selected live workflows
passed through 2026-10-06. API coverage describes routing and request schemas;
live eligibility is verified only for selected workflows. Local results do not
establish hosted CI success or publication. See [tests/README.md](tests/README.md).

Selected account/property access lifecycles passed for single and batch create,
role replacement, delete and empty-role deletion, preserving existing administrators.

### Known limitations

- Chat is experimental and disabled by default. A live request returned HTTP 500
  without a session ID; the creation outcome is unknown and was not replayed.
  Session creation and continuation remain unverified.
- Account provisioning's redirect/consent flow and Analytics 360 have not been
  live-tested.
- Quota failure handling uses synthetic fixtures; live quota exhaustion is untested.
  Other preview eligibility and business rules remain method-specific.
- Universal Analytics, Measurement Protocol collection, BigQuery export queries
  and remote MCP transports are outside the project scope.
