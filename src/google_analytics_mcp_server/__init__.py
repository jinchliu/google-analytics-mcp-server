"""Google Analytics MCP server entry point."""

from importlib.metadata import PackageNotFoundError, version


def package_version() -> str:
    """Return the installed distribution version."""
    try:
        return version('google-analytics-mcp-server')
    except PackageNotFoundError:
        return '0.1.0'


def main() -> None:
    """Start the stdio server without writing application output to stdout."""
    from google_analytics_mcp_server.server import run_server

    run_server()
