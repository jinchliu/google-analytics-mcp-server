"""Pinned, versioned discovery catalog and reviewed tool ownership."""

import json
import re
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any, cast

from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server.config import ApiVersion, Version

SERVICES = {'admin': 'analyticsadmin', 'data': 'analyticsdata'}
VERSIONS: tuple[Version, ...] = ('v1beta', 'v1alpha')


@dataclass(frozen=True)
class Method:
    """A concrete discovery method; identity always includes its version."""

    api: str
    version: Version
    id: str
    resource: str
    name: str
    spec: dict[str, Any]
    owner: str

    @property
    def logical_key(self) -> str:
        """Return the version-independent identity."""
        return f'{self.api}:{self.id}'

    @property
    def key(self) -> str:
        """Return the fully qualified identity."""
        return f'{self.api}:{self.version}:{self.id}'

    @property
    def parameters(self) -> dict[str, Any]:
        """Return method-specific path and query parameters."""
        return dict(self.spec.get('parameters', {}))

    @property
    def path_parameters(self) -> dict[str, Any]:
        """Return path parameters, retaining their wire names."""
        return {k: v for k, v in self.parameters.items() if v['location'] == 'path'}

    @property
    def request_schema(self) -> str | None:
        """Return the request schema name when a body is supported."""
        return cast(str | None, self.spec.get('request', {}).get('$ref'))

    @property
    def response_schema(self) -> str | None:
        """Return the response schema name."""
        return cast(str | None, self.spec.get('response', {}).get('$ref'))

    def schema(self, name: str) -> dict[str, Any]:
        """Resolve a schema in this method's exact discovery version."""
        return cast(dict[str, Any], document(self.api, self.version)['schemas'][name])


@cache
def document(api: str, version: str) -> dict[str, Any]:
    """Load a bundled discovery snapshot without network access."""
    if api not in SERVICES or version not in VERSIONS:
        raise ToolError('Use api=admin|data and version=v1alpha|v1beta.')
    path = files('google_analytics_mcp_server').joinpath(
        'discovery', f'{SERVICES[api]}.{version}.json'
    )
    result: dict[str, Any] = json.loads(path.read_text('utf-8'))
    return result


@cache
def routes() -> dict[str, dict[str, str]]:
    """Load reviewed default versions and logical owners."""
    result: dict[str, dict[str, str]] = json.loads(
        files('google_analytics_mcp_server')
        .joinpath('discovery', 'routes.json')
        .read_text('utf-8')
    )
    return result


@cache
def methods() -> tuple[Method, ...]:
    """Enumerate every versioned endpoint, including non-default variants."""
    found: list[Method] = []

    def visit(api: str, version: Version, node: dict[str, Any], resource: str) -> None:
        for name, spec in node.get('methods', {}).items():
            identity = spec['id'].removeprefix(f'{SERVICES[api]}.')
            key = f'{api}:{identity}'
            if key not in routes():
                raise ToolError(
                    f'{key} needs a reviewed entry in discovery/routes.json.'
                )
            found.append(
                Method(
                    api, version, identity, resource, name, spec, routes()[key]['tool']
                )
            )
        for name, child in node.get('resources', {}).items():
            visit(api, version, child, '.'.join(filter(None, (resource, name))))

    for api in SERVICES:
        for version in VERSIONS:
            visit(api, version, document(api, version), '')
    return tuple(found)


def method(api: str, identity: str, version: ApiVersion = 'auto') -> Method:
    """Resolve a method without falling back from an explicitly selected version."""
    identity = identity.removeprefix(f'{SERVICES.get(api, api)}.')
    route = routes().get(f'{api}:{identity}')
    if route is None:
        raise ToolError(f'Unknown {api} method {identity}. Use ga_describe_schema.')
    chosen = route['default_version'] if version == 'auto' else version
    for item in methods():
        if (item.api, item.id, item.version) == (api, identity, chosen):
            return item
    raise ToolError(
        f'{api}:{identity} is unavailable in {chosen}; choose its published version.'
    )


def candidates(owner: str, version: ApiVersion = 'auto') -> list[Method]:
    """Return the routes served by one native tool in the requested version."""
    return [
        item
        for item in methods()
        if item.owner == owner
        and (
            item.version == version
            if version != 'auto'
            else item.version == routes()[item.logical_key]['default_version']
        )
    ]


def items_key(item: Method) -> str:
    """Find the collection in a list response."""
    fields = item.schema(item.response_schema or '').get('properties', {})
    arrays: list[str] = [k for k, v in fields.items() if v.get('type') == 'array']
    if len(arrays) != 1:
        raise ToolError(f'{item.key} does not have an unambiguous list response.')
    return arrays[0]


def short_schema(name: str) -> str:
    """Remove the Admin version prefix from a schema name."""
    return re.sub(r'^GoogleAnalyticsAdminV1(?:alpha|beta)', '', name)


def describe(
    name: str, api: str | None = None, version: ApiVersion = 'auto'
) -> dict[str, Any]:
    """Describe an exact method or schema with references, scopes and ownership."""
    parts = name.split(':')
    if len(parts) == 3:
        api, selected, name = parts
        if selected not in VERSIONS:
            raise ToolError('Qualified names use admin|data:v1alpha|v1beta:name.')
        version = selected
    elif len(parts) == 2:
        api, name = parts
        if '.' in api:
            api, selected = api.split('.', 1)
            if selected not in VERSIONS:
                raise ToolError(
                    'Qualified names use admin.v1alpha:Name or data.v1beta:Name.'
                )
            version = selected
    for alias, service_name in SERVICES.items():
        if name.startswith(f'{service_name}.'):
            api = alias
            name = name.removeprefix(f'{service_name}.')
            break
    if api is not None and api not in SERVICES:
        raise ToolError('api must be admin or data.')
    matches = [m for m in methods() if (api is None or m.api == api) and m.id == name]
    if matches:
        apis = {m.api for m in matches}
        if len(apis) != 1:
            raise ToolError('Qualify the method with api=admin or data.')
        chosen = method(next(iter(apis)), name, version)
        return {
            'method': chosen.key,
            'tool': chosen.owner,
            'available_versions': sorted({m.version for m in matches}),
            **chosen.spec,
            'request_schema': chosen.schema(chosen.request_schema)
            if chosen.request_schema
            else None,
            'response_schema': chosen.schema(chosen.response_schema)
            if chosen.response_schema
            else None,
        }
    schemas: list[tuple[str, Version, str, dict[str, Any]]] = []
    for service in SERVICES:
        if api and service != api:
            continue
        for selected in VERSIONS:
            if version != 'auto' and version != selected:
                continue
            for identity, schema in document(service, selected)['schemas'].items():
                if name in {identity, short_schema(identity)}:
                    schemas.append((service, selected, identity, schema))
    if not schemas:
        raise ToolError(
            f'Unknown schema or method: {name}. Use a discovery name, e.g. Audience or properties.runReport.'
        )
    if len({s[0] for s in schemas}) > 1:
        raise ToolError(f'{name} exists in both APIs. Specify api=admin or data.')
    service, selected, identity, schema = schemas[0]
    return {'api': service, 'api_version': selected, 'name': identity, **schema}
