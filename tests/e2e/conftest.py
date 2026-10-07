"""Shared fixtures for explicitly selected live verification."""

import re
from pathlib import Path

import pytest
from tests.e2e.sandbox import Sandbox

from google_analytics_mcp_server.server import create_server


@pytest.fixture
def property_name(pytestconfig):
    value = pytestconfig.getoption('--property')
    if not value:
        pytest.fail('Live tests require --property=PROPERTY_ID.')
    name = value if value.startswith('properties/') else f'properties/{value}'
    if not re.fullmatch(r'properties/[0-9]+', name):
        pytest.fail('Supply a numeric property ID or properties/PROPERTY_ID.')
    return name


@pytest.fixture
def server():
    return create_server()


@pytest.fixture
def sandbox(pytestconfig, property_name):
    plan = pytestconfig.getoption('--sandbox-plan')
    journal = pytestconfig.getoption('--sandbox-journal')
    if not plan or not journal:
        pytest.fail('Live writes require --sandbox-plan and --sandbox-journal.')
    return Sandbox(Path(plan), Path(journal), property_name)
