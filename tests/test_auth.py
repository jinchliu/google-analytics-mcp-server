"""Lazy credential lookup and bounded, stdin-isolated gcloud discovery."""

import subprocess
from unittest.mock import Mock

import google.auth
import pytest
from google.auth.credentials import AnonymousCredentials
from google.auth.exceptions import DefaultCredentialsError
from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import auth


def test_credentials_are_lazy_cached_and_scoped(monkeypatch):
    credentials = AnonymousCredentials()
    default = Mock(return_value=(credentials, 'synthetic-project'))
    monkeypatch.setattr(google.auth, 'default', default)
    assert auth._credentials is None
    assert auth.credentials() is credentials
    assert auth.credentials() is credentials
    default.assert_called_once_with(scopes=auth.scopes())
    assert len(auth.scopes()) == 3


def test_chat_scope_is_only_requested_for_enabled_writable_profile(monkeypatch):
    assert not any('chatbot' in scope for scope in auth.scopes())
    monkeypatch.setenv('GA_MCP_ENABLE_CHAT', 'true')
    assert any('chatbot' in scope for scope in auth.scopes())
    monkeypatch.setenv('GA_MCP_READ_ONLY', '1')
    assert not any('chatbot' in scope for scope in auth.scopes())


@pytest.mark.parametrize(
    'error,hint',
    [
        (DefaultCredentialsError('missing'), 'No usable Google credentials'),
        (subprocess.TimeoutExpired('gcloud', 30), 'timed out'),
    ],
)
def test_failed_credentials_offer_setup_and_restore_helper(error, hint, monkeypatch):
    from google.auth import _cloud_sdk

    original = _cloud_sdk._run_subprocess_ignore_stderr
    monkeypatch.setattr(google.auth, 'default', Mock(side_effect=error))
    with pytest.raises(ToolError, match=hint):
        auth.credentials()
    assert _cloud_sdk._run_subprocess_ignore_stderr is original
    assert auth._credentials is None


def test_gcloud_lookup_never_inherits_mcp_stdin(monkeypatch):
    check_output = Mock(return_value=b'synthetic-project')
    monkeypatch.setattr(subprocess, 'check_output', check_output)
    assert (
        auth._gcloud_output(['gcloud', 'config', 'get', 'project'])
        == b'synthetic-project'
    )
    check_output.assert_called_once_with(
        ['gcloud', 'config', 'get', 'project'],
        stdin=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
