"""Lossless report summaries and small resource navigation projections."""

from typing import Any

SLIM_FIELDS: dict[str, tuple[str, ...]] = {
    'accounts': ('name', 'displayName', 'regionCode', 'createTime'),
    'properties': (
        'name',
        'displayName',
        'propertyType',
        'parent',
        'timeZone',
        'currencyCode',
    ),
    'customDimensions': ('name', 'parameterName', 'displayName', 'scope'),
    'customMetrics': (
        'name',
        'parameterName',
        'displayName',
        'scope',
        'measurementUnit',
    ),
    'keyEvents': ('name', 'eventName', 'countingMethod', 'custom', 'deletable'),
    'conversionEvents': ('name', 'eventName', 'countingMethod', 'custom', 'deletable'),
    'dataStreams': (
        'name',
        'displayName',
        'type',
        'webStreamData.measurementId',
        'webStreamData.defaultUri',
    ),
    'accessBindings': ('name', 'user', 'roles'),
    'measurementProtocolSecrets': ('name', 'displayName'),
    'audiences': ('name', 'displayName', 'description', 'membershipDurationDays'),
    'audienceExports': ('name', 'audience', 'audienceDisplayName', 'state', 'rowCount'),
    'audienceLists': ('name', 'audience', 'audienceDisplayName', 'state', 'rowCount'),
    'recurringAudienceLists': (
        'name',
        'audience',
        'audienceDisplayName',
        'activeDaysRemaining',
    ),
    'reportTasks': (
        'name',
        'reportMetadata.state',
        'reportMetadata.taskRowCount',
        'reportMetadata.totalRowCount',
    ),
    'accountSummaries': ('name', 'account', 'displayName', 'propertySummaries'),
    'adSenseLinks': ('name', 'adClientCode'),
    'bigQueryLinks': (
        'name',
        'project',
        'datasetLocation',
        'dailyExportEnabled',
        'streamingExportEnabled',
        'freshDailyExportEnabled',
    ),
    'calculatedMetrics': (
        'name',
        'calculatedMetricId',
        'displayName',
        'formula',
        'metricUnit',
        'invalidMetricReference',
    ),
    'channelGroups': ('name', 'displayName', 'description', 'systemDefined', 'primary'),
    'eventCreateRules': ('name', 'destinationEvent', 'sourceCopyParameters'),
    'eventEditRules': ('name', 'displayName', 'processingOrder'),
    'expandedDataSets': (
        'name',
        'displayName',
        'dimensionNames',
        'metricNames',
        'dataCollectionStartTime',
    ),
    'firebaseLinks': ('name', 'project', 'createTime'),
    'googleAdsLinks': (
        'name',
        'customerId',
        'adsPersonalizationEnabled',
        'canManageClients',
    ),
    'reportingDataAnnotations': (
        'name',
        'title',
        'annotationDate',
        'annotationDateRange',
        'color',
        'systemGenerated',
    ),
    'rollupPropertySourceLinks': ('name', 'sourceProperty'),
    'searchAds360Links': (
        'name',
        'advertiserId',
        'advertiserDisplayName',
        'adsPersonalizationEnabled',
    ),
    'subpropertyEventFilters': ('name', 'applyToProperty'),
    'subpropertySyncConfigs': (
        'name',
        'applyToProperty',
        'customDimensionAndMetricSyncMode',
    ),
    'displayVideo360AdvertiserLinks': (
        'name',
        'advertiserId',
        'advertiserDisplayName',
        'adsPersonalizationEnabled',
    ),
    'displayVideo360AdvertiserLinkProposals': (
        'name',
        'advertiserId',
        'advertiserDisplayName',
        'linkProposalStatusDetails',
    ),
    'sKAdNetworkConversionValueSchema': ('name', 'applyConversionValues'),
}
DEFAULT_FIELDS = ('name', 'displayName', 'description', 'state')


def dotted(value: dict[str, Any], path: str) -> Any:
    """Read a nested field without confusing false/zero with absence."""
    current: Any = value
    for part in path.split('.'):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def resources(collection: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project navigation rows, including accounts without properties."""
    if collection == 'accountSummaries':
        result = []
        for account in rows:
            base = {
                'name': account.get('name'),
                'account': account.get('account'),
                'accountName': account.get('displayName'),
            }
            for prop in account.get('propertySummaries') or [{}]:
                result.append(
                    {
                        **base,
                        'property': prop.get('property'),
                        'propertyName': prop.get('displayName'),
                        'propertyType': prop.get('propertyType'),
                    }
                )
        return result
    fields = SLIM_FIELDS.get(collection, DEFAULT_FIELDS)
    result = []
    for row in rows:
        slim = {
            field: dotted(row, field)
            for field in fields
            if dotted(row, field) is not None
        }
        # Link collections often identify their targets without a display name.
        if collection not in SLIM_FIELDS:
            slim.update(
                {
                    k: v
                    for k, v in row.items()
                    if k.endswith(('Id', 'Link', 'Property'))
                    and isinstance(v, (str, int, bool))
                }
            )
        result.append(slim)
    return result


def row_values(row: dict[str, Any]) -> list[Any]:
    """Keep API string values intact, including large integers and decimal metrics."""
    return [
        v.get('value', '')
        for v in (*row.get('dimensionValues', []), *row.get('metricValues', []))
    ]


def report(response: dict[str, Any]) -> dict[str, Any]:
    """Build compact tables without dropping metadata or aggregate rows."""
    if 'reports' in response:
        return {**response, 'reports': [report(child) for child in response['reports']]}
    if 'funnelTable' in response or 'funnelVisualization' in response:
        return {
            key: report(value)
            if key in {'funnelTable', 'funnelVisualization'}
            else value
            for key, value in response.items()
        }
    if 'audienceRows' in response:
        audience = response.get('audienceExport', response.get('audienceList', {}))
        return {
            **{k: v for k, v in response.items() if k != 'audienceRows'},
            'columns': [d['dimensionName'] for d in audience.get('dimensions', [])],
            'rows': [row_values(row) for row in response['audienceRows']],
        }
    dimensions = [
        d.get('name', d.get('dimensionName', ''))
        for d in response.get('dimensionHeaders', [])
    ]
    metrics = [
        m.get('name', m.get('metricName', ''))
        for m in response.get('metricHeaders', [])
    ]
    if dimensions or metrics:
        result = {
            k: v
            for k, v in response.items()
            if k not in {'rows', 'dimensionHeaders', 'metricHeaders'}
        }
        result['columns'] = dimensions + metrics
        result['rows'] = [row_values(row) for row in response.get('rows', [])]
        result['metric_types'] = [
            m.get('type') for m in response.get('metricHeaders', [])
        ]
        return result
    return response
