# google-analytics-mcp-server

A stdio [MCP](https://modelcontextprotocol.io/) server for Google Analytics with
full coverage of the [Admin](https://developers.google.com/analytics/devguides/config/admin/v1)
and [Data](https://developers.google.com/analytics/devguides/reporting/data/v1) APIs.
Empower your AI agent to explore data, run reports, manage configuration and
control access in Google Analytics. 🚀

## ✨ Highlights

- **Full API surface, compact tool set.** 28 tools cover the pinned Admin and Data
  alpha/beta APIs, with 26 enabled by default.
- **Built for context windows.** Returns Markdown tables by default, with `json`
  and `json_full` available.
- **Controlled writes.** Writable leaf patches, confirmation for sensitive
  operations and a strict read-only mode.
- **Runs as you.** Your own OAuth client through Application Default Credentials
  (ADC).

Compared with [Google's official MCP](https://github.com/googleanalytics/google-analytics-mcp),
this server adds configuration and access management, pivot/batch reports and async
jobs.

## 🧰 Tools

| Tier | Tools |
|---|---|
| Read (13) | `ga_list`, `ga_get`, `ga_query`, `ga_run_report`, `ga_run_realtime_report`, `ga_run_pivot_report`, `ga_batch_run_reports`, `ga_run_funnel_report`, `ga_check_compatibility`, `ga_get_metadata`, `ga_run_access_report`, `ga_search_change_history`, `ga_describe_schema` |
| Write (6) | `ga_create`, `ga_update`, `ga_provision_account_ticket`, `ga_create_rollup_property`, `ga_provision_subproperty`, `ga_reorder_event_edit_rules` |
| Destructive (6), explicit confirm required | `ga_delete`, `ga_update_settings`, `ga_manage_access_bindings`, `ga_submit_user_deletion`, `ga_acknowledge_user_data_collection`, `ga_review_dv360_link_proposal` |
| Job (1) | `ga_start_async_job` |
| Optional (2) | `ga_chat` (job/session), `ga_call_api` (mixed effects) |

Chat and raw API access are optional. For feature settings and strict read-only
mode, see [Appendix A: Environment Variables](#appendix-a-environment-variables).

Available operations depend on your Google Analytics permissions and property
eligibility. See [Coverage and known limitations](#-coverage-and-known-limitations)
for current limitations.

## 🔑 Setup

You need a Google Cloud project and the
[gcloud CLI](https://cloud.google.com/sdk/docs/install).

### 1. Enable the APIs

Enable both APIs on the project that will carry the quota:

```bash
gcloud services enable analyticsadmin.googleapis.com analyticsdata.googleapis.com --project=YOUR_PROJECT
```

### 2. Create an OAuth client

Create a **Desktop app** OAuth client in your Google Cloud project and download
its JSON file. See [Manage OAuth Clients](https://support.google.com/cloud/answer/15549257).

- If your OAuth app's user type is set to **External**, check its publishing
  status before logging in.
- With the Analytics scopes used here, refresh tokens issued for an **External**
  app in **Testing** expire after seven days. For ongoing use, switch to
  **In production**. See [Google's OAuth guidance](https://developers.google.com/identity/protocols/oauth2#expiration).

### 3. Authorize access

Log in with the scopes needed for your work. Remove the scopes you don't need.
A limited grant produces an error when a tool requires an additional scope:

```bash
gcloud auth application-default login \
  --client-id-file=YOUR_DESKTOP_CLIENT.json \
  --scopes=https://www.googleapis.com/auth/analytics.readonly,\
https://www.googleapis.com/auth/analytics.edit,\
https://www.googleapis.com/auth/analytics.manage.users,\
https://www.googleapis.com/auth/analytics.chatbot.read,\
https://www.googleapis.com/auth/cloud-platform
```

| Scope | Unlocks |
|---|---|
| `analytics.readonly` | Most resource reads and reports |
| `analytics.edit` | Configuration writes and change-history reads |
| `analytics.manage.users` | Access-binding reads and writes |
| `analytics.manage.users.readonly` | Access-binding reads with limited credentials |
| `analytics.chatbot.read` | Optional [Analytics Chat](https://developers.google.com/analytics/devguides/reporting/data/v1/advisor-basics) |
| `cloud-platform` | [Cloud quota-project setup](https://docs.cloud.google.com/docs/authentication/troubleshoot-adc) where required; grants no GA access |

For limited read credentials, use:

- `https://www.googleapis.com/auth/analytics.readonly`
- `https://www.googleapis.com/auth/analytics.manage.users.readonly`

Change-history reads still require `analytics.edit`.

Already using [google-tag-manager-mcp](https://github.com/jinchliu/google-tag-manager-mcp)?
If both servers read the same ADC file, include both sets in one login:

```bash
gcloud auth application-default login \
  --client-id-file=YOUR_DESKTOP_CLIENT.json \
  --scopes=https://www.googleapis.com/auth/analytics.readonly,\
https://www.googleapis.com/auth/analytics.edit,\
https://www.googleapis.com/auth/analytics.manage.users,\
https://www.googleapis.com/auth/analytics.chatbot.read,\
https://www.googleapis.com/auth/tagmanager.readonly,\
https://www.googleapis.com/auth/tagmanager.edit.containers,\
https://www.googleapis.com/auth/tagmanager.delete.containers,\
https://www.googleapis.com/auth/tagmanager.edit.containerversions,\
https://www.googleapis.com/auth/tagmanager.publish,\
https://www.googleapis.com/auth/tagmanager.manage.users,\
https://www.googleapis.com/auth/tagmanager.manage.accounts,\
https://www.googleapis.com/auth/cloud-platform
```

Enable chat with `GA_MCP_ENABLE_CHAT=1` after authorization.
See [Appendix A: Environment Variables](#appendix-a-environment-variables) for the full list.

To use different identities or grants, set `GOOGLE_APPLICATION_CREDENTIALS` for
each MCP process to its ADC credentials file.

## 🔌 Connect an MCP client

Install from PyPI with [uv](https://docs.astral.sh/uv/guides/tools/) (recommended) or pipx:

```bash
uv tool install --python 3.14 google-analytics-mcp-server
```

The client examples below pin an explicit quota project. The identity needs
permission to consume services on it.

The quota project is selected in this order:

1. `GOOGLE_CLOUD_QUOTA_PROJECT` overrides credential configuration.
2. Otherwise, a configured ADC `quota_project_id` is used.
3. Without an explicit quota project, attribution depends on the credentials and
   API, commonly the OAuth client's project.

### Claude

Claude Code:

```bash
claude mcp add --scope user google-analytics-mcp-server \
  -e GOOGLE_CLOUD_QUOTA_PROJECT=YOUR_PROJECT \
  -- google-analytics-mcp-server
```

Claude Desktop:

Open **Settings > Developer > Edit Config** and add:

```json
{
  "mcpServers": {
    "google-analytics-mcp-server": {
      "command": "google-analytics-mcp-server",
      "env": { "GOOGLE_CLOUD_QUOTA_PROJECT": "YOUR_PROJECT" }
    }
  }
}
```

If Claude Desktop cannot find the command, use its absolute path instead.

### ChatGPT / Codex

In the desktop app, go to **Settings > MCP servers > Add server** and choose
**STDIO**. Use `google-analytics-mcp-server` as the command.

Or add the server with the Codex CLI:

```bash
codex mcp add google-analytics-mcp-server \
  --env GOOGLE_CLOUD_QUOTA_PROJECT=YOUR_PROJECT \
  -- google-analytics-mcp-server
```

### Try it out

After connecting the server, try asking your AI agent:

- “List my Google Analytics accounts and properties.”
- “Compare purchase revenue by country over the last 28 days.”
- “Create an event-scoped custom dimension for `membership_level`.”

## 🛡️ Safety model

- **Your approval.** Sensitive actions require you to approve what will change
  and where.
- **Read-only mode.** Limits your agent to viewing data and running reports;
  changes, new background jobs and chat are disabled.
- **Access management.** Specify every role a user should have when changing
  their access. Removing all roles deletes that direct access assignment.
- **Sensitive output.** Detailed results may include Measurement Protocol API
  secrets. Summary lists hide those values.

## 🧪 Coverage and known limitations

This server is in alpha. It supports the [bundled Admin/Data API versions](https://github.com/jinchliu/google-analytics-mcp-server/blob/main/src/google_analytics_mcp_server/discovery/SOURCES.md).
Features depend on your Analytics permissions and property eligibility.

- **Chat:** Experimental and disabled by default. Successful sessions remain
  unverified.
- **Account provisioning and Analytics 360:** These workflows
  remain unverified; see [known limitations](https://github.com/jinchliu/google-analytics-mcp-server/blob/main/CHANGELOG.md#known-limitations).
- **Outside scope:** Universal Analytics, Measurement Protocol event collection
  and BigQuery export queries.

## Development

For development from a source checkout:

```bash
uv sync --locked
uv run pytest
```

Tests run offline by default. See the [test guide](https://github.com/jinchliu/google-analytics-mcp-server/blob/main/tests/README.md) for packaging
checks and opt-in live tests.

## License

MIT, see [LICENSE](https://github.com/jinchliu/google-analytics-mcp-server/blob/main/LICENSE).

## Appendix A: Environment Variables

| Variable | Default | Effect |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | unset | Standard ADC credentials file, before the gcloud ADC file |
| `GOOGLE_CLOUD_QUOTA_PROJECT` | unset | Quota project override |
| `GOOGLE_CLOUD_PROJECT` | unset | Optional project ID; avoids gcloud project lookup |
| `GA_MCP_READ_ONLY` | `0` | Registers the 13 read tools and blocks mutations at execution |
| `GA_MCP_ENABLE_CHAT` | `0` | Enables experimental chat outside read-only mode; requires its extra scope |
| `GA_MCP_ENABLE_RAW_API` | `0` | Enables `ga_call_api` outside read-only mode |
| `GA_MCP_MAX_LIST_ITEMS` | `1000` | Maximum requested list size; upstream limits also apply |
| `GA_MCP_MAX_ROWS` | `10000` | Aggregate requested report/query rows; pivot limits multiply |
| `GA_MCP_MAX_OUTPUT_BYTES` | `65536` | Combined text and structured read-output budget |
| `GA_MCP_HTTP_TIMEOUT` | `60` | Timeout in seconds per HTTP request |
| `GA_MCP_LOG_LEVEL` | `INFO` | Log level; output goes to stderr |

Flags accept `1`, `true` or `yes`. Restart after environment changes.
