"""Reporting adapters with explicit versions and complete official body support."""

from typing import Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from google_analytics_mcp_server import catalog, client, contracts, names, render
from google_analytics_mcp_server.config import ApiVersion, ResponseFormat
from google_analytics_mcp_server.registry import tool


def run(
    name: str,
    property: str,
    body: dict[str, Any],
    fmt: ResponseFormat,
    version: ApiVersion,
) -> CallToolResult:
    """Apply a method-specific adapter before the shared transport."""
    item = catalog.method('data', f'properties.{name}', version)
    params = {'property': names.property_name(property)}
    payload = contracts.report_body(item, body, params)
    return render.report_result(
        item, payload, client.execute(item, params, payload), fmt
    )


@tool()
def ga_run_report(
    property: str,
    metrics: list[str | dict[str, Any]] | None = None,
    dimensions: list[str | dict[str, Any]] | None = None,
    date_ranges: list[dict[str, Any]] | None = None,
    dimension_filter: dict[str, Any] | None = None,
    metric_filter: dict[str, Any] | None = None,
    order_bys: list[dict[str, Any]] | None = None,
    limit: int | None = None,
    offset: int | None = None,
    keep_empty_rows: bool | None = None,
    metric_aggregations: list[str] | None = None,
    comparisons: list[dict[str, Any]] | None = None,
    cohort_spec: dict[str, Any] | None = None,
    currency_code: str | None = None,
    conversion_spec: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    response_format: ResponseFormat = 'markdown',
    *,
    api_version: ApiVersion = 'auto',
) -> CallToolResult:
    """Run a GA4 report with named metrics/dimensions or a full official body.

    Defaults: 100 rows and 28daysAgo..yesterday, except cohort requests omit
    dateRanges. Use metadata and check_compatibility for valid field names.
    Alpha conversionSpec requires api_version=v1alpha. No parameter is silently
    overwritten: provide each field either in body or through its shortcut.

    Args:
        property: properties/N or its numeric ID.
        metrics: Names or full Metric objects; may instead be supplied in body.
        dimensions: Names or full Dimension objects.
        date_ranges: Explicit date ranges; incompatible with cohort_spec.
        dimension_filter: Official FilterExpression on dimensions.
        metric_filter: Official FilterExpression on metrics.
        order_bys: Official OrderBy objects.
        limit: Requested rows, default 100.
        offset: Starting row, default zero.
        keep_empty_rows: Preserve zero metric rows when supported.
        metric_aggregations: Requested totals/minima/maxima.
        comparisons: Official comparison definitions.
        cohort_spec: Cohort request; omit date_ranges.
        currency_code: Currency override.
        conversion_spec: Alpha conversion reporting options.
        body: Complete version-specific body, including fields without shortcuts.
        response_format: markdown, json or json_full.
        api_version: auto, v1beta or v1alpha.
    """
    payload = contracts.merge_body(
        body,
        metrics=contracts.dimensions_metrics(metrics),
        dimensions=contracts.dimensions_metrics(dimensions),
        dateRanges=date_ranges,
        dimensionFilter=dimension_filter,
        metricFilter=metric_filter,
        orderBys=order_bys,
        limit=limit,
        offset=offset,
        keepEmptyRows=keep_empty_rows,
        metricAggregations=metric_aggregations,
        comparisons=comparisons,
        cohortSpec=cohort_spec,
        currencyCode=currency_code,
        conversionSpec=conversion_spec,
    )
    return run('runReport', property, payload, response_format, api_version)


@tool()
def ga_run_realtime_report(
    property: str,
    metrics: list[str | dict[str, Any]] | None = None,
    dimensions: list[str | dict[str, Any]] | None = None,
    limit: int | None = None,
    body: dict[str, Any] | None = None,
    response_format: ResponseFormat = 'markdown',
    *,
    api_version: ApiVersion = 'auto',
) -> CallToolResult:
    """Run a realtime report. No offset pagination is supported.

    Args:
        property: properties/N or its numeric ID.
        metrics: Metric names or official Metric objects.
        dimensions: Dimension names or official Dimension objects.
        limit: Maximum rows, default 100.
        body: Full body for minuteRanges, filters, ordering, aggregation and quota.
        response_format: markdown, json or json_full.
        api_version: Published version or reviewed default.
    """
    payload = contracts.merge_body(
        body,
        metrics=contracts.dimensions_metrics(metrics),
        dimensions=contracts.dimensions_metrics(dimensions),
        limit=limit,
    )
    return run('runRealtimeReport', property, payload, response_format, api_version)


@tool()
def ga_run_pivot_report(
    property: str,
    body: dict[str, Any],
    response_format: ResponseFormat = 'markdown',
    *,
    api_version: ApiVersion = 'auto',
) -> CallToolResult:
    """Run a pivot report using the full official body.

    Each pivot has its own limit and offset; limits multiply against the row budget.
    Args:
        property: properties/N or its numeric ID.
        body: Dimensions, metrics, pivots and all other official request fields.
        response_format: markdown, json or json_full.
        api_version: Published version or reviewed default.
    """
    return run('runPivotReport', property, body, response_format, api_version)


@tool()
def ga_batch_run_reports(
    property: str,
    body: dict[str, Any],
    pivot: bool = False,
    response_format: ResponseFormat = 'markdown',
    *,
    api_version: ApiVersion = 'auto',
) -> CallToolResult:
    """Run up to five standard or pivot reports in one API request.

    Args:
        property: properties/N or its numeric ID.
        body: Official batch body containing requests, each with its own options.
        pivot: Select batchRunPivotReports instead of batchRunReports.
        response_format: markdown, json or json_full.
        api_version: Published version or reviewed default.
    """
    return run(
        'batchRunPivotReports' if pivot else 'batchRunReports',
        property,
        body,
        response_format,
        api_version,
    )


@tool()
def ga_run_funnel_report(
    property: str,
    body: dict[str, Any],
    response_format: ResponseFormat = 'markdown',
    *,
    api_version: ApiVersion = 'auto',
) -> CallToolResult:
    """Run an alpha funnel report; no offset pagination is supported.

    Args:
        property: properties/N or its numeric ID.
        body: Official Funnel request, including steps, breakdown and segments.
        response_format: markdown, json or json_full.
        api_version: auto or v1alpha for the pinned API surface.
    """
    return run('runFunnelReport', property, body, response_format, api_version)


@tool()
def ga_check_compatibility(
    property: str,
    body: dict[str, Any],
    *,
    api_version: ApiVersion = 'auto',
) -> dict[str, Any]:
    """Check compatibility of dimensions, metrics and filters before a report.

    Args:
        property: properties/N or its numeric ID.
        body: Full CheckCompatibilityRequest; discover names with ga_get_metadata.
        api_version: Published version or reviewed default.
    """
    item = catalog.method('data', 'properties.checkCompatibility', api_version)
    return render.entity(
        client.execute(item, {'property': names.property_name(property)}, body), item
    )


@tool()
def ga_get_metadata(
    property: str,
    search: str | None = None,
    kind: Literal['all', 'dimensions', 'metrics', 'conversions'] = 'all',
    custom_only: bool = False,
    response_format: ResponseFormat = 'markdown',
    *,
    api_version: ApiVersion = 'auto',
) -> CallToolResult:
    """Discover report field names; property=0 requests universal metadata.

    Args:
        property: Property name/ID, optionally ending in /metadata.
        search: Case-insensitive search across field metadata.
        kind: all, dimensions, metrics or alpha conversions.
        custom_only: Return custom definitions only.
        response_format: json_full retains the entire unfiltered API response.
        api_version: Use v1alpha for conversion metadata; auto defaults to beta.
    """
    item = catalog.method('data', 'properties.getMetadata', api_version)
    if kind == 'conversions' and item.version != 'v1alpha':
        raise ToolError('Conversion metadata requires api_version=v1alpha.')
    prop = names.property_name(property.removesuffix('/metadata'))
    response = client.execute(item, {'name': f'{prop}/metadata'})
    if response_format == 'json_full':
        return render.result(response)
    selected: dict[str, Any] = {
        'api_version': item.version,
        'name': response.get('name', f'{prop}/metadata'),
    }
    for key, values in response.items():
        if not isinstance(values, list) or (kind != 'all' and key != kind):
            continue
        selected[key] = [
            v
            for v in values
            if (not custom_only or v.get('customDefinition'))
            and (search is None or search.casefold() in render.json_text(v).casefold())
        ]
    return render.result(
        selected,
        markdown='\n\n'.join(
            f'{key}\n{render.table(value)}'
            for key, value in selected.items()
            if isinstance(value, list)
        )
        if response_format == 'markdown'
        else None,
    )
