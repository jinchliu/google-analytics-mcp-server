"""Independent discovery enumeration, routing and packaged snapshot integrity."""

import hashlib
from collections import Counter
from importlib.resources import files

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import catalog
from google_analytics_mcp_server.registry import REGISTRY
from tests.helpers import path_params


def test_discovery_and_routes_have_identical_endpoint_sets():
    expected = set()

    def visit(api, version, node):
        expected.update(
            (api, version, spec['id'].removeprefix(catalog.SERVICES[api] + '.'))
            for spec in node.get('methods', {}).values()
        )
        for child in node.get('resources', {}).values():
            visit(api, version, child)

    for api in catalog.SERVICES:
        for version in catalog.VERSIONS:
            visit(api, version, catalog.document(api, version))
    actual = {(item.api, item.version, item.id) for item in catalog.methods()}
    assert actual == expected
    assert len(actual) == len(catalog.methods()) == 248
    assert {f'{api}:{identity}' for api, _, identity in expected} == set(
        catalog.routes()
    )
    assert len(catalog.routes()) == 191
    assert Counter((api, version) for api, version, _ in actual) == {
        ('admin', 'v1beta'): 55,
        ('admin', 'v1alpha'): 166,
        ('data', 'v1beta'): 11,
        ('data', 'v1alpha'): 16,
    }


@pytest.mark.parametrize('item', catalog.methods(), ids=lambda item: item.key)
def test_reviewed_routes_schemas_and_path_patterns(item):
    assert item.owner in REGISTRY
    default = catalog.method(item.api, item.id)
    assert default.version == catalog.routes()[item.logical_key]['default_version']
    assert catalog.method(item.api, item.id, item.version) == item
    for schema in (item.request_schema, item.response_schema):
        if schema:
            assert item.schema(schema)
    import re

    for name, value in path_params(item).items():
        assert re.fullmatch(item.path_parameters[name]['pattern'], value)
    if item.owner == 'ga_list':
        assert catalog.items_key(item)


def test_default_versions_and_explicit_no_fallback():
    assert (
        catalog.method('admin', 'accounts.searchChangeHistoryEvents').version
        == 'v1alpha'
    )
    assert catalog.method('data', 'properties.runReport').version == 'v1beta'
    with pytest.raises(ToolError, match='unavailable in v1beta'):
        catalog.method('data', 'properties.runFunnelReport', 'v1beta')
    with pytest.raises(ToolError, match='Unknown'):
        catalog.method('admin', 'properties.notReal')


@pytest.mark.parametrize(
    'name', ['data:v1alpha:RunReportRequest', 'data.v1alpha:RunReportRequest']
)
def test_qualified_schema_resolution(name):
    described = catalog.describe(name)
    assert described['api'] == 'data'
    assert described['api_version'] == 'v1alpha'
    assert 'conversionSpec' in described['properties']
    assert (
        'conversionSpec'
        not in catalog.describe('RunReportRequest', 'data', 'v1beta')['properties']
    )


def test_snapshot_hashes_match_documented_sources():
    root = files('google_analytics_mcp_server').joinpath('discovery')
    sources = root.joinpath('SOURCES.md').read_text()
    for api in catalog.SERVICES.values():
        for version in catalog.VERSIONS:
            data = root.joinpath(f'{api}.{version}.json').read_bytes()
            assert hashlib.sha256(data).hexdigest() in sources


def test_unreviewed_method_drift_fails_closed(monkeypatch):
    original = catalog.routes()
    monkeypatch.setattr(
        catalog,
        'routes',
        lambda: {k: v for k, v in original.items() if k != 'data:properties.runReport'},
    )
    catalog.methods.cache_clear()
    try:
        with pytest.raises(ToolError, match='reviewed entry'):
            catalog.methods()
    finally:
        catalog.methods.cache_clear()
