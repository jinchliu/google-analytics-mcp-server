"""MCP application factory and stdio transport."""

import logging
import os
import sys

from mcp.server.mcpserver import MCPServer

from google_analytics_mcp_server import config, package_version
from google_analytics_mcp_server.registry import REGISTRY, annotations, available

SERVER_NAME = 'google-analytics-mcp-server'
SERVER_TITLE = 'Google Analytics'
SERVER_DESCRIPTION = 'A stdio MCP server for Google Analytics with full coverage of the Admin and Data APIs.'

# Keep the instructions within the project's 2,048-byte compatibility budget.
INSTRUCTIONS = """\
Google Analytics Admin and Data APIs. Copy full resource names from results,
such as properties/123/keyEvents/456. Numeric IDs work only in parameters with
one known resource type. Start with ga_list('accountSummaries'). Lists return
one page; continue with next_page_token and the same query settings.

API tools accept api_version=auto, v1beta or v1alpha; raw calls need an explicit
version. Auto uses reviewed defaults. Use an explicit version for extra alpha
fields. ga_describe_schema shows fields and examples. Bodies use API camelCase
JSON. Conflicting body and shortcut fields are errors.

Reports: find dimension/metric names with ga_get_metadata and validate them
with ga_check_compatibility. Default limit is 100. Dates default to 28daysAgo
through yesterday only without cohortSpec. Cohort reports omit dateRanges.
Follow each result's continuation hint: realtime/funnel have no offset paging;
pivots page per pivot. Quota is shown only when supplied by the API. Check the
project/property balances before more calls; do not retry exhausted quotas.

Async flow: ga_start_async_job, then ga_get until ACTIVE, then ga_query.
Report tasks expose state under reportMetadata. Retain the job name on timeout;
do not create it again. Read-only mode excludes job creation and chat sessions.

Configuration writes take effect without a publish step. ga_update uses leaf
masks; supported null clears are explicit, arrays follow each method's rules.
Access writes use ga_manage_access_bindings; empty roles delete the binding.
Confirmation-required operations include access writes, deletion/archive,
settings, user deletion, data-collection acknowledgement and DV360 decisions.
Only set confirm=true after user authorization for that operation.
ga_call_api, if enabled, uses the same policy and feature gates.
"""


def create_server() -> MCPServer[None]:
    """Create a server with the current feature profile and lazy authentication."""
    import google_analytics_mcp_server.tools  # noqa: F401

    config.validate_limits()
    server: MCPServer[None] = MCPServer(
        name=SERVER_NAME,
        title=SERVER_TITLE,
        description=SERVER_DESCRIPTION,
        version=package_version(),
        instructions=INSTRUCTIONS,
    )
    for entry in REGISTRY.values():
        if available(entry):
            server.tool(annotations=annotations(entry))(entry.fn)
    return server


def run_server() -> None:
    """Run the local server; stdout is reserved for MCP protocol messages."""
    logging.basicConfig(
        level=os.environ.get('GA_MCP_LOG_LEVEL', 'INFO').upper(),
        stream=sys.stderr,
        format='%(levelname)s %(name)s: %(message)s',
    )
    logging.getLogger('googleapiclient').setLevel(logging.ERROR)
    create_server().run(transport='stdio')
