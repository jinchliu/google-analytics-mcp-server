"""Offline by default: forbid ADC and network access, keep live tests opt-in."""

import json
import os
import socket
from types import SimpleNamespace

import google.auth
import httplib2
import pytest
from googleapiclient.discovery import build_from_document

from google_analytics_mcp_server import auth, catalog, client
from tests.helpers import FakeClock, RecordingHttp


def pytest_addoption(parser):
    parser.addoption(
        '--live',
        action='store_true',
        help='Run explicitly selected read-only Google API tests with ADC.',
    )
    parser.addoption(
        '--property',
        default=os.environ.get('GA_MCP_TEST_PROPERTY'),
        help='GA4 property name or ID for read-only live reports.',
    )
    parser.addoption(
        '--live-write', action='store_true', help='Opt into reviewed sandbox writes.'
    )
    parser.addoption('--sandbox-plan', help='Locally validated sandbox plan JSON.')
    parser.addoption('--sandbox-journal', help='Durable sandbox execution journal.')
    parser.addoption('--account', help='Explicit account target for access tests.')


def pytest_collection_modifyitems(config, items):
    if config.getoption('--live-write') and not config.getoption('--live'):
        raise pytest.UsageError('--live-write also requires --live.')
    selected, deselected = [], []
    for item in items:
        excluded = (
            item.get_closest_marker('live') and not config.getoption('--live')
        ) or (
            item.get_closest_marker('live_write')
            and not config.getoption('--live-write')
        )
        (deselected if excluded else selected).append(item)
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)


@pytest.fixture(autouse=True)
def isolated_process(monkeypatch, request):
    for key in tuple(os.environ):
        if key.startswith('GA_MCP_'):
            monkeypatch.delenv(key)
    monkeypatch.setattr(auth, '_credentials', None)
    monkeypatch.setattr(client, '_services', {})
    if request.node.get_closest_marker('live'):
        monkeypatch.setenv(
            'GA_MCP_READ_ONLY',
            '0' if request.node.get_closest_marker('live_write') else '1',
        )
        return

    def forbidden(*args, **kwargs):
        raise AssertionError('Offline tests must not load ADC or access the network.')

    monkeypatch.setattr(google.auth, 'default', forbidden)
    monkeypatch.setattr(httplib2.Http, 'request', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


@pytest.fixture
def http(monkeypatch):
    transport = RecordingHttp()
    services = {}
    for api in catalog.SERVICES:
        for version in catalog.VERSIONS:
            services[api, version] = build_from_document(
                json.dumps(catalog.document(api, version)), http=transport
            )
    monkeypatch.setattr(client, '_services', services)
    return transport


@pytest.fixture
def full_profile(monkeypatch):
    monkeypatch.setenv('GA_MCP_ENABLE_CHAT', '1')
    monkeypatch.setenv('GA_MCP_ENABLE_RAW_API', '1')


@pytest.fixture
def clock(monkeypatch):
    from google_analytics_mcp_server.tools import jobs

    clock = FakeClock()
    fake_time = SimpleNamespace(sleep=clock.sleep, monotonic=clock.monotonic)
    monkeypatch.setattr(client, 'time', fake_time)
    monkeypatch.setattr(jobs, 'time', fake_time)
    monkeypatch.setattr(client.random, 'random', lambda: 0.0)
    return clock


@pytest.fixture
def anyio_backend():
    return 'asyncio'
