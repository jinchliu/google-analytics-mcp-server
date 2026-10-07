"""Unambiguous resource names and useful locator errors."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import catalog, names
from tests.helpers import path_params


@pytest.mark.parametrize(
    'value,expected',
    [('123', 'properties/123'), (' /properties/123/ ', 'properties/123')],
)
def test_property_normalization(value, expected):
    assert names.property_name(value) == expected


@pytest.mark.parametrize(
    'value',
    [
        'G-EXAMPLE',
        'UA-123-1',
        'https://analytics.google.com',
        'accounts/123',
        'properties/1/dataStreams/2',
    ],
)
def test_bad_property_names_are_rejected(value):
    with pytest.raises(ToolError):
        names.property_name(value)


@pytest.mark.parametrize(
    'item',
    [
        m
        for m in catalog.methods()
        if m.owner
        in {'ga_get', 'ga_update', 'ga_update_settings', 'ga_delete', 'ga_query'}
    ],
    ids=lambda item: item.key,
)
def test_resource_patterns_dispatch_uniquely(item):
    params = path_params(item)
    selected, resolved = names.select(
        item.owner, locator=next(iter(params.values())), api_version=item.version
    )
    assert selected == item
    assert resolved == params


def test_ambiguous_numeric_get_does_not_guess_account_or_property():
    with pytest.raises(ToolError, match='No unique'):
        names.select('ga_get', locator='123')
