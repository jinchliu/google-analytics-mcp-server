"""Recursive request schema coverage, separately from endpoint dispatch tests."""

import inspect

import pytest

from google_analytics_mcp_server import capabilities, catalog, contracts
from google_analytics_mcp_server.registry import REGISTRY
from tests.helpers import scalar


def schema_leaves(item):
    def walk(spec, path, ancestors):
        if '$ref' in spec:
            ref = spec['$ref']
            if ref in ancestors:
                yield path, {'type': 'object'}
                return
            yield from walk(item.schema(ref), path, ancestors | {ref})
        elif spec.get('type') == 'array':
            yield from walk(spec['items'], path + '[]', ancestors)
        elif spec.get('properties'):
            for name, child in spec['properties'].items():
                yield from walk(child, f'{path}.{name}' if path else name, ancestors)
        else:
            yield path, spec

    return (
        list(walk({'$ref': item.request_schema}, '', frozenset()))
        if item.request_schema
        else []
    )


@pytest.mark.parametrize('item', catalog.methods(), ids=lambda item: item.key)
def test_all_recursive_fields_and_query_routes_are_declared(item):
    fields = capabilities.request_fields(item)
    expected = dict(schema_leaves(item))
    assert len(fields) == len(expected)
    assert {field['path'] for field in fields} == set(expected)
    for field in fields:
        assert field['raw'] == 'body.' + field['path']
        assert field['native'] == 'generated' or field['native'].endswith(
            '.' + field['path']
        )
        assert isinstance(field['writable'], bool)
    endpoint = capabilities.endpoint(item)
    signature = inspect.signature(REGISTRY[item.owner].fn)
    for name, route in endpoint['parameters'].items():
        assert route['raw'] == f'params.{name}'
        assert (
            route['native'].startswith('raw.params.')
            or route['native'] in signature.parameters
        )
    assert (
        not set(endpoint['global_query_parameters']) & capabilities.TRANSPORT_OVERRIDES
    )
    assert endpoint['native_tool'] == item.owner


FIELD_CASES = [
    pytest.param(item, path, spec, id=f'{item.key}:{path}')
    for item in catalog.methods()
    for path, spec in schema_leaves(item)
]


@pytest.mark.parametrize('item,path,spec', FIELD_CASES)
def test_recursive_field_values_survive_validation(item, path, spec):
    value = scalar(spec)
    if spec.get('format') == 'int64':
        value = '9007199254740993'
    if 'pattern' in spec:
        # Patterns constrain identifiers independently of primitive types.
        value = (
            spec['pattern'].removeprefix('^').removesuffix('$').replace('[^/]+', '123')
        )
    body = value
    for part in reversed(path.split('.')):
        if part.endswith('[]'):
            body = [body]
            part = part[:-2]
        if part:
            body = {part: body}
    assert contracts.validate(body, {'$ref': item.request_schema}, item) == body
