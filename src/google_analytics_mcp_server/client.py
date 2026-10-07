"""One authorized execution path with policy, serialization and bounded retries."""

import json
import random
import threading
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httplib2
from google.auth.exceptions import RefreshError, TransportError
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build_from_document, fix_method_name, key2param
from googleapiclient.errors import HttpError
from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import (
    auth,
    catalog,
    config,
    contracts,
    package_version,
    policy,
)
from google_analytics_mcp_server.capabilities import TRANSPORT_OVERRIDES
from google_analytics_mcp_server.catalog import Method

_lock = threading.RLock()
_services: dict[tuple[str, str], Any] = {}


def service(item: Method) -> Any:
    """Build the exact vendored service while holding the transport lock."""
    key = (item.api, item.version)
    if key not in _services:
        transport = httplib2.Http(timeout=config.positive_int('HTTP_TIMEOUT', 60))
        _services[key] = build_from_document(
            json.dumps(catalog.document(*key)),
            http=AuthorizedHttp(auth.credentials(), http=transport),
        )
    return _services[key]


def execute(
    item: Method,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    """Send a validated request; native and raw calls both enter here."""
    config.validate_limits()
    wire_params = dict(params or {})
    policy.guard(item, wire_params, body, confirm)
    available = {
        **catalog.document(item.api, item.version).get('parameters', {}),
        **item.parameters,
    }
    unknown = set(wire_params) - set(available)
    if unknown:
        raise ToolError(
            f'{item.key} has no query/path parameter: {", ".join(sorted(unknown))}.'
        )
    if set(wire_params) & TRANSPORT_OVERRIDES:
        raise ToolError(
            'Authentication and upload transport overrides are not supported; configure ADC on the server.'
        )
    if wire_params.get('alt', 'json') != 'json':
        raise ToolError('MCP responses require alt=json.')
    for key, spec in item.parameters.items():
        if spec.get('required') and key not in wire_params:
            raise ToolError(f'{item.key} requires parameter {key}.')
    wire_params = {
        k: contracts.validate(
            v,
            {'type': 'array', 'items': available[k]}
            if available[k].get('repeated')
            else available[k],
            item,
            k,
        )
        for k, v in wire_params.items()
    }
    if body is not None and not item.request_schema:
        raise ToolError(f'{item.id} does not accept a body.')
    if item.request_schema:
        body = contracts.validate(
            body if body is not None else {}, {'$ref': item.request_schema}, item
        )
        contracts.enforce_request(item, body, wire_params)
    # Run the policy on the exact normalized payload that is about to be sent.
    policy.guard(item, wire_params, body, confirm)
    with _lock:
        try:
            resource = service(item)
            for part in item.resource.split('.'):
                resource = getattr(resource, fix_method_name(part))()
            kwargs = {key2param(k): v for k, v in wire_params.items()}
            if body is not None:
                kwargs['body'] = body
            request = getattr(resource, fix_method_name(item.name))(**kwargs)
            request.headers['user-agent'] = (
                f'google-analytics-mcp-server/{package_version()}'
            )
            return _send(request, item)
        except RefreshError as error:
            raise ToolError(
                f'Google credentials could not refresh. {auth.setup_hint()}'
            ) from error
        except (TypeError, ValueError) as error:
            raise ToolError(f'{item.key}: {error}') from error
        except (OSError, httplib2.HttpLib2Error, TransportError) as error:
            hint = (
                ''
                if policy.is_read(item)
                else ' The outcome may be unknown; inspect the target before repeating this mutation.'
            )
            raise ToolError(
                f'Network request to {catalog.SERVICES[item.api]}.googleapis.com failed: {error}.{hint}'
            ) from error


def _retry_delay(error: HttpError, attempt: int) -> float:
    value = error.resp.get('retry-after')
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(
                    0.0,
                    (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds(),
                )
            except ValueError, TypeError:
                pass
    return min(16.0, 2.0**attempt + random.random())


def _retry_limit(error: HttpError, item: Method) -> int:
    if not policy.is_read(item):
        return 0
    status = int(error.resp.status)
    text = error.content.decode('utf-8', errors='replace').lower()
    if status in {429, 403}:
        if any(
            word in text
            for word in (
                'token',
                'daily',
                'per day',
                'per hour',
                'threshold',
                'server error',
            )
        ):
            return 0
        if 'concurrent' in text:
            return 3
        if item.api == 'admin' and any(
            word in text for word in ('ratelimitexceeded', 'rate limit', 'per minute')
        ):
            return 5
        return 0
    if 500 <= status < 600:
        return 1 if item.api == 'data' or item.name == 'runAccessReport' else 3
    return 0


def _send(request: Any, item: Method) -> dict[str, Any]:
    attempt = 0
    while True:
        try:
            response = request.execute(num_retries=0)
            return response if isinstance(response, dict) else {'response': response}
        except HttpError as error:
            if attempt < _retry_limit(error, item):
                delay = _retry_delay(error, attempt)
                if delay <= 30:
                    time.sleep(delay)
                    attempt += 1
                    continue
            message = error.content.decode('utf-8', errors='replace')
            try:
                data = json.loads(message).get('error', {})
                message = data.get('message', message)
            except json.JSONDecodeError, AttributeError:
                pass
            hints = ''
            lower = error.content.decode('utf-8', errors='replace').lower()
            if 'scope' in lower:
                hints = f' Required scopes: {item.spec.get("scopes", [])}. {auth.setup_hint()}'
            elif 'service_disabled' in lower or 'accessnotconfigured' in lower:
                hints = f' Enable {catalog.SERVICES[item.api]}.googleapis.com on the project named in the error.'
            elif error.resp.status == 403:
                hints = ' Check the account/property role and any Analytics 360 or preview eligibility.'
            elif error.resp.status == 400:
                hints = ' Inspect ga_describe_schema; report dimensions/metrics can be checked with ga_get_metadata and ga_check_compatibility.'
            elif error.resp.status == 404:
                hints = ' Copy an existing resource name from ga_list.'
            elif error.resp.status == 429:
                hints = ' Do not retry exhausted or unknown quotas. Hourly quotas refresh within an hour, not necessarily at a whole-hour boundary.'
            if error.resp.status >= 500 and not policy.is_read(item):
                hints += ' The mutation was not retried. Its outcome may be unknown; inspect the target before repeating it.'
            raise ToolError(
                f'{item.key}: HTTP {error.resp.status}: {message}.{hints}'
            ) from error
