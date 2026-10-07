"""Every published example is checked recursively against its exact versions."""

import json
from dataclasses import replace
from importlib.resources import files

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import catalog, contracts
from google_analytics_mcp_server.tools.meta import ga_describe_schema


def cases():
    for api in catalog.SERVICES:
        examples = json.loads(
            files('google_analytics_mcp_server')
            .joinpath('examples', f'{api}.json')
            .read_text()
        )
        for name, entries in examples.items():
            for index, entry in enumerate(entries):
                for version in entry['api_versions']:
                    yield pytest.param(
                        api, version, name, entry, id=f'{api}:{version}:{name}:{index}'
                    )


@pytest.mark.parametrize('api,version,name,entry', list(cases()))
def test_all_packaged_examples(api, version, name, entry):
    resolved = catalog.describe(name, api, version)
    context = next(
        item
        for item in catalog.methods()
        if item.api == api and item.version == version
    )
    item = replace(context, spec={'request': {'$ref': resolved['name']}})
    assert (
        contracts.validate(entry['body'], {'$ref': item.request_schema}, item)
        == entry['body']
    )
    assert entry in ga_describe_schema(name, api, api_version=version)['examples']


def test_admin_filter_examples_use_filter_expressions():
    example = ga_describe_schema('Audience', 'admin', api_version='v1alpha')[
        'examples'
    ][0]['body']
    item = catalog.method('admin', 'properties.audiences.create')
    group = example['filterClauses'][0]['simpleFilter']['filterExpression']['andGroup']
    group['expressions'] = group.pop('filterExpressions')
    with pytest.raises(ToolError, match='expressions'):
        contracts.validate(example, {'$ref': item.request_schema}, item)
