"""Version-specific request validation, report defaults and patch masks."""

import copy
import math
import re
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import config
from google_analytics_mcp_server.catalog import Method


def validate(value: Any, spec: dict[str, Any], item: Method, path: str = 'body') -> Any:
    """Validate known schema shape and serialize int64 without losing precision.

    Google remains authoritative for business rules absent from discovery.
    """
    if '$ref' in spec:
        spec = item.schema(spec['$ref'])
    if not spec or spec.get('type') == 'any':
        return value
    if value is None:
        raise ToolError(
            f'{path}: null is not a wire value; use documented empty/default values.'
        )
    kind = spec.get('type')
    if kind == 'object' or 'properties' in spec:
        if not isinstance(value, dict):
            raise ToolError(f'{path} must be an object.')
        props = spec.get('properties', {})
        extra = spec.get('additionalProperties')
        result = {}
        for key, child in value.items():
            child_spec = props.get(key, extra)
            if child_spec is None:
                raise ToolError(
                    f'{path}.{key} is not in {item.api} {item.version}; use ga_describe_schema and an explicit version for alpha fields.'
                )
            result[key] = validate(child, child_spec, item, f'{path}.{key}')
        for key in (
            spec.get('required', []) if isinstance(spec.get('required'), list) else []
        ):
            if key not in result:
                raise ToolError(f'{path}.{key} is required.')
        return result
    if kind == 'array':
        if not isinstance(value, list):
            raise ToolError(f'{path} must be an array.')
        return [
            validate(v, spec.get('items', {}), item, f'{path}[{i}]')
            for i, v in enumerate(value)
        ]
    if spec.get('format') in {'int64', 'uint64'}:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, str))
            or not re.fullmatch(r'-?\d+', str(value))
        ):
            raise ToolError(f'{path} must be an int64 integer or decimal string.')
        number = int(value)
        low, high = (
            (0, 2**64 - 1) if spec['format'] == 'uint64' else (-(2**63), 2**63 - 1)
        )
        if not low <= number <= high:
            raise ToolError(f'{path} is outside its {spec["format"]} range.')
        value = str(number)
    elif kind == 'string' and not isinstance(value, str):
        raise ToolError(f'{path} must be a string.')
    elif kind == 'boolean' and not isinstance(value, bool):
        raise ToolError(f'{path} must be a boolean.')
    elif kind == 'integer' and (isinstance(value, bool) or not isinstance(value, int)):
        raise ToolError(f'{path} must be an integer.')
    elif kind == 'number' and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ToolError(f'{path} must be a finite number.')
    if 'enum' in spec and value not in spec['enum']:
        raise ToolError(f'{path} must be one of {spec["enum"]}.')
    if (
        'pattern' in spec
        and isinstance(value, str)
        and not re.fullmatch(spec['pattern'], value)
    ):
        raise ToolError(f'{path} must match {spec["pattern"]}.')
    if 'minimum' in spec and float(value) < float(spec['minimum']):
        raise ToolError(f'{path} violates minimum={spec["minimum"]}.')
    if 'maximum' in spec and float(value) > float(spec['maximum']):
        raise ToolError(f'{path} violates maximum={spec["maximum"]}.')
    return value


def merge_body(body: dict[str, Any] | None, **shortcuts: Any) -> dict[str, Any]:
    """Combine body and explicitly supplied shortcuts; duplicate keys are errors."""
    result = copy.deepcopy(body or {})
    for key, value in shortcuts.items():
        if value is not None:
            if key in result:
                raise ToolError(
                    f'{key} was supplied both in body and as an argument; choose one.'
                )
            result[key] = value
    return result


def dimensions_metrics(
    value: list[str | dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Allow plain names alongside full official dimension/metric objects."""
    return (
        [{'name': entry} if isinstance(entry, str) else entry for entry in value]
        if value is not None
        else None
    )


def integer(value: Any, name: str, minimum: int = 0) -> int:
    """Require a whole nonnegative decimal without silently rounding."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, str))
        or not re.fullmatch(r'\d+', str(value))
    ):
        raise ToolError(f'{name} must be an integer >= {minimum}.')
    number = int(value)
    if number < minimum:
        raise ToolError(f'{name} must be >= {minimum}.')
    return number


def report_body(
    item: Method, body: dict[str, Any], params: dict[str, Any]
) -> dict[str, Any]:
    """Apply only defaults supported by this method and enforce row budgets."""
    result = copy.deepcopy(body)
    row_cap = config.positive_int('MAX_ROWS', 10000)
    fields = item.schema(item.request_schema or '').get('properties', {})
    children = (
        result.get('requests')
        if item.name in {'batchRunReports', 'batchRunPivotReports'}
        else None
    )
    if children is not None:
        if not isinstance(children, list) or not 1 <= len(children) <= 5:
            raise ToolError('Batch reports require 1-5 requests.')
        from google_analytics_mcp_server.catalog import method

        child_name = (
            'runPivotReport' if item.name == 'batchRunPivotReports' else 'runReport'
        )
        child_method = method('data', f'properties.{child_name}', item.version)
        normalized = []
        for child in children:
            if not isinstance(child, dict):
                raise ToolError('Each batch report request must be an object.')
            if child.get('property') not in (None, params.get('property')):
                raise ToolError(
                    'Batch request property must match the enclosing property.'
                )
            normalized.append(report_body(child_method, child, params))
        result['requests'] = normalized
        requested = sum(row_budget(child) for child in normalized)
    else:
        if 'cohortSpec' in result and 'dateRanges' in result:
            raise ToolError('Cohort reports must omit dateRanges.')
        if (
            'dateRanges' in fields
            and 'dateRanges' not in result
            and 'cohortSpec' not in result
        ):
            result['dateRanges'] = [{'startDate': '28daysAgo', 'endDate': 'yesterday'}]
        if 'limit' in fields:
            result.setdefault('limit', 100)
            integer(result['limit'], 'limit', 1)
        if 'offset' in result:
            integer(result['offset'], 'offset')
        elif 'offset' in fields:
            result['offset'] = 0
        if 'pivots' in result:
            if not isinstance(result['pivots'], list) or not result['pivots']:
                raise ToolError('pivots must be a nonempty array.')
            for pivot in result['pivots']:
                if not isinstance(pivot, dict):
                    raise ToolError('Each pivot must be an object.')
                pivot.setdefault('limit', 100)
                integer(pivot['limit'], 'pivot.limit', 1)
                integer(pivot.get('offset', 0), 'pivot.offset')
        if 'returnPropertyQuota' in fields:
            result.setdefault('returnPropertyQuota', True)
        if 'returnEntityQuota' in fields:
            account = str(params.get('entity', '')).startswith('accounts/')
            if account and result.get('returnEntityQuota') is True:
                raise ToolError(
                    'Account access reports require returnEntityQuota=false.'
                )
            result.setdefault('returnEntityQuota', not account)
        requested = row_budget(result)
    if requested > row_cap:
        raise ToolError(
            f'Requested {requested} rows exceed GA_MCP_MAX_ROWS={row_cap}; reduce limits or increase the configured budget.'
        )
    return result


def row_budget(body: dict[str, Any]) -> int:
    """Count aggregate requested rows, including the product of pivot limits."""
    if 'pivots' in body:
        return math.prod(
            integer(p.get('limit', 100), 'pivot.limit', 1) for p in body['pivots']
        )
    return integer(body.get('limit', 0), 'limit')


def enforce_request(item: Method, body: dict[str, Any], params: dict[str, Any]) -> None:
    """Enforce shared semantic limits without injecting defaults into raw calls."""
    report_owners = {
        'ga_run_report',
        'ga_run_realtime_report',
        'ga_run_pivot_report',
        'ga_batch_run_reports',
        'ga_run_funnel_report',
        'ga_run_access_report',
        'ga_query',
    }
    if item.owner not in report_owners:
        return
    batch = item.owner == 'ga_batch_run_reports'
    records = body.get('requests', []) if batch else [body]
    total = 0
    if batch and not 1 <= len(records) <= 5:
        raise ToolError('Batch reports require 1-5 requests.')
    for record in records:
        if 'dateRanges' in record and 'cohortSpec' in record:
            raise ToolError('Cohort reports must omit dateRanges.')
        if 'limit' in record:
            integer(record['limit'], 'limit', 1)
        if 'offset' in record:
            integer(record['offset'], 'offset')
        if 'pivots' in record:
            if not record['pivots']:
                raise ToolError('pivots must be a nonempty array.')
            for pivot in record['pivots']:
                if 'limit' not in pivot:
                    raise ToolError(
                        'Each raw pivot requires an explicit positive limit.'
                    )
                integer(pivot['limit'], 'pivot.limit', 1)
                integer(pivot.get('offset', 0), 'pivot.offset')
        if (
            str(params.get('entity', '')).startswith('accounts/')
            and record.get('returnEntityQuota') is True
        ):
            raise ToolError('Account access reports require returnEntityQuota=false.')
        if record.get('property') not in (None, params.get('property')):
            raise ToolError('Report body.property must match the path property.')
        # Raw calls retain Google's 10,000-row default rather than adding a field.
        total += (
            row_budget(record) if 'limit' in record or 'pivots' in record else 10000
        )
    cap = config.positive_int('MAX_ROWS', 10000)
    if total > cap:
        raise ToolError(f'Requested {total} rows exceed GA_MCP_MAX_ROWS={cap}.')


def page_size(item: Method, limit: int) -> int:
    """Use reviewed per-collection limits, never parse discovery prose at runtime."""
    cap = config.positive_int('MAX_LIST_ITEMS', 1000)
    if not 1 <= limit <= cap:
        raise ToolError(f'limit must be between 1 and GA_MCP_MAX_LIST_ITEMS={cap}.')
    collection = item.resource.split('.')[-1]
    maximum = {
        'accessBindings': 500,
        'measurementProtocolSecrets': 10,
        'audienceExports': 1000,
        'audienceLists': 1000,
        'recurringAudienceLists': 1000,
        'reportTasks': 1000,
    }.get(collection, 200)
    return min(limit, maximum)


def snake(value: str) -> str:
    """Convert a discovery JSON field to its protobuf spelling."""
    return re.sub(r'(?<!^)(?=[A-Z])', '_', value).lower()


def patch_body(
    item: Method, patch: dict[str, Any], mask: str | None
) -> tuple[dict[str, Any], str]:
    """Generate leaf masks and reject unverified implicit clearing.

    Args:
        item: The exact versioned update method.
        patch: Changes in official JSON field spelling.
        mask: Optional explicit leaf paths in JSON or protobuf spelling.
    """
    if not patch or not item.request_schema:
        raise ToolError('Supply a nonempty patch.')
    paths: list[str] = []
    body = copy.deepcopy(patch)

    def walk(value: dict[str, Any], spec: dict[str, Any], prefix: str = '') -> None:
        for key, child in list(value.items()):
            field = spec.get('properties', {}).get(key)
            path = f'{prefix}.{key}' if prefix else key
            if field is None:
                raise ToolError(f'Unknown patch field {path} for {item.version}.')
            if field.get('readOnly') or re.search(
                r'\bImmutable\.', field.get('description', '')
            ):
                raise ToolError(f'{path} is output-only or immutable.')
            if child is None:
                # This clear is explicitly documented as accepting an empty URI.
                if (
                    path == 'webStreamData.defaultUri'
                    and item.resource == 'properties.dataStreams'
                ):
                    value[key] = ''
                else:
                    raise ToolError(
                        f'Clearing {path} via null is not verified. Supply the documented explicit empty/default value.'
                    )
            if '$ref' in field:
                if not isinstance(child, dict) or not child:
                    raise ToolError(
                        f'{path} requires a nonempty object of leaf changes.'
                    )
                walk(child, item.schema(field['$ref']), path)
            else:
                paths.append(path)

    walk(body, item.schema(item.request_schema))
    aliases = {snake(path): path for path in paths}
    aliases.update({path: path for path in paths})
    if mask is not None:
        supplied = [part.strip() for part in mask.split(',')]
        if any(path not in aliases for path in supplied) or {
            aliases[path] for path in supplied
        } != set(paths):
            raise ToolError(
                'update_mask must identify exactly the supplied leaf changes. Broad masks require ga_call_api and confirmation.'
            )
        resolved = ','.join(supplied)
    else:
        # JSON FieldMask encoding uses lowerCamelCase; explicit wire spelling is preserved.
        resolved = ','.join(paths)
    return body, resolved
