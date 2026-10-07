"""Lazy, process-local Application Default Credentials."""

import subprocess
import threading
from unittest.mock import patch

import google.auth
from google.auth.credentials import Credentials
from google.auth.exceptions import DefaultCredentialsError
from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import config

PREFIX = 'https://www.googleapis.com/auth/'
BASE_SCOPES = ('analytics.readonly', 'analytics.edit', 'analytics.manage.users')
_lock = threading.Lock()
_credentials: Credentials | None = None


def _gcloud_output(command: list[str]) -> bytes:
    """Keep google-auth's project lookup subprocess off MCP's stdin."""
    return subprocess.check_output(
        command, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30
    )


def scopes() -> tuple[str, ...]:
    """Request chat scope only when the chat tool can be registered."""
    extra = (
        ('analytics.chatbot.read',)
        if config.enabled('ENABLE_CHAT') and not config.read_only()
        else ()
    )
    return tuple(PREFIX + scope for scope in (*BASE_SCOPES, *extra))


def setup_hint() -> str:
    """Explain explicit user login without performing it."""
    return (
        'Enable analyticsadmin.googleapis.com and analyticsdata.googleapis.com in your project. '
        'Create a Desktop OAuth client and run:\n'
        'gcloud auth application-default login --client-id-file=YOUR_DESKTOP_CLIENT.json '
        f'--scopes={",".join(scopes())}\n'
        'If this ADC file is shared with GTM or other applications, include their scopes too. '
        'Alternatively set GOOGLE_APPLICATION_CREDENTIALS per MCP process. '
        'Passing scopes in code does not grant additional consent to existing user credentials.'
    )


def credentials() -> Credentials:
    """Resolve ADC only on the first permitted API request."""
    global _credentials
    with _lock:
        if _credentials is None:
            try:
                # The installed google-auth helper inherits stdin by default.
                # Patch only its project-lookup helper, not process-wide Popen.
                with patch(
                    'google.auth._cloud_sdk._run_subprocess_ignore_stderr',
                    _gcloud_output,
                ):
                    _credentials, _ = google.auth.default(scopes=scopes())
            except DefaultCredentialsError as error:
                raise ToolError(
                    f'No usable Google credentials. {setup_hint()}'
                ) from error
            except subprocess.TimeoutExpired as error:
                raise ToolError(
                    'gcloud project lookup timed out. Set GOOGLE_CLOUD_PROJECT explicitly or repair the local gcloud setup.'
                ) from error
        return _credentials
