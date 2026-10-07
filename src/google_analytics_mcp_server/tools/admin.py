"""Admin API custom methods, sensitive settings and access management."""

import re
from typing import Any, Literal
from urllib.parse import quote

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from google_analytics_mcp_server import (
    catalog,
    client,
    config,
    contracts,
    names,
    render,
)
from google_analytics_mcp_server.config import ApiVersion, ResponseFormat
from google_analytics_mcp_server.registry import tool

ACKNOWLEDGEMENT = (
    'I acknowledge that I have the necessary privacy disclosures and rights from my '
    'end users for the collection and processing of their data, including the association '
    'of such data with the visitation information Google Analytics collects from my site '
    'and/or app property.'
)


@tool()
def ga_update_settings(
    name: str,
    patch: dict[str, Any],
    confirm: bool = False,
    update_mask: str | None = None,
    *,
    api_version: ApiVersion = 'auto',
) -> dict[str, Any]:
    """Update attribution, retention, Google signals, reporting identity,
    data redaction or enhanced measurement settings after confirmation.

    Args:
        name: Full settings singleton name, discoverable with ga_get.
        patch: Writable fields in official JSON spelling.
        confirm: True only after user approval of the specific settings change.
        update_mask: Optional mask matching precisely the supplied leaf changes.
        api_version: Reviewed default or explicit version.
    """
    item, params = names.select(
        'ga_update_settings', locator=name, api_version=api_version
    )
    body, mask = contracts.patch_body(item, patch, update_mask)
    params['updateMask'] = mask
    return render.entity(
        client.execute(item, params, body, confirm=confirm), item, mutating=True
    )


@tool()
def ga_manage_access_bindings(
    operation: Literal[
        'create', 'update', 'delete', 'batch_create', 'batch_update', 'batch_delete'
    ],
    parent: str | None = None,
    name: str | None = None,
    body: dict[str, Any] | None = None,
    confirm: bool = False,
    *,
    api_version: ApiVersion = 'auto',
) -> dict[str, Any]:
    """Manage account/property user access using a single or true batch API call.

    All operations require confirmation. Supply the complete intended role set;
    an empty roles list deletes the binding. No implicit read/merge is performed.

    Args:
        operation: create/update/delete or batch_create/batch_update/batch_delete.
        parent: accounts/N or properties/N for creates and batches.
        name: Full accessBindings resource name for a single update/delete.
        body: Full official body. Batches use requests with accessBinding or name.
        confirm: True only after user approval of all targets and roles.
        api_version: auto or the published version (v1alpha in the baseline).
    """
    actions = {
        'create': 'create',
        'update': 'patch',
        'delete': 'delete',
        'batch_create': 'batchCreate',
        'batch_update': 'batchUpdate',
        'batch_delete': 'batchDelete',
    }
    single_named = operation in {'update', 'delete'}
    if single_named and (not name or parent is not None):
        raise ToolError('A single update/delete takes name, without parent.')
    if not single_named and (not parent or name is not None):
        raise ToolError('Creates and batches take parent, without name.')
    if operation != 'delete' and body is None:
        raise ToolError('Supply the official request body.')
    item, params = names.select(
        'ga_manage_access_bindings',
        locator=name if single_named else parent,
        action=actions[operation],
        api_version=api_version,
    )
    response = client.execute(item, params, body, confirm=confirm)
    return render.entity(response, item, mutating=True)


@tool()
def ga_run_access_report(
    entity: str,
    body: dict[str, Any],
    limit: int | None = None,
    offset: int | None = None,
    response_format: ResponseFormat = 'markdown',
    *,
    api_version: ApiVersion = 'auto',
) -> CallToolResult:
    """Report data-access records for an account or property.

    Requires the relevant Administrator role. Account requests cannot request
    quota; property requests use returnEntityQuota and response.quota.
    Args:
        entity: Full accounts/N or properties/N name; numeric IDs are ambiguous.
        body: Complete official AccessReport body with dimensions and metrics.
        limit: Optional row count shortcut; default 100.
        offset: Optional starting row shortcut.
        response_format: markdown, json or json_full.
        api_version: Reviewed default or explicit version.
    """
    if not re.fullmatch(r'(accounts|properties)/[^/]+', entity):
        raise ToolError('entity must be accounts/N or properties/N.')
    item, params = names.select(
        'ga_run_access_report', locator=entity, api_version=api_version
    )
    payload = contracts.report_body(
        item, contracts.merge_body(body, limit=limit, offset=offset), params
    )
    return render.report_result(
        item, payload, client.execute(item, params, payload), response_format
    )


@tool()
def ga_search_change_history(
    account: str,
    body: dict[str, Any] | None = None,
    response_format: ResponseFormat = 'markdown',
    *,
    api_version: ApiVersion = 'auto',
) -> CallToolResult:
    """Search one page of account change history; requires analytics.edit.

    Args:
        account: accounts/N or its numeric ID.
        body: Full request with filters, pageSize and pageToken. Default size 100.
        response_format: markdown, json or json_full.
        api_version: auto selects alpha to retain additional resource types.
    """
    item = catalog.method('admin', 'accounts.searchChangeHistoryEvents', api_version)
    params = {'account': names.normalize(account, 'accounts')}
    payload = dict(body or {})
    payload.setdefault('pageSize', 100)
    if (
        not 1
        <= contracts.integer(payload['pageSize'], 'pageSize', 1)
        <= min(200, config.positive_int('MAX_LIST_ITEMS', 1000))
    ):
        raise ToolError(
            'Change-history pageSize must fit the 200-item API limit and configured list budget.'
        )
    response = client.execute(item, params, payload)
    token = response.get('nextPageToken')
    result = {
        **response,
        '_mcp': {
            'api_version': item.version,
            'has_more': bool(token),
            'next_arguments': {
                'account': params['account'],
                'body': {**payload, 'pageToken': token},
                'api_version': item.version,
                'response_format': response_format,
            }
            if token
            else None,
        },
    }
    return render.result(
        result,
        markdown=render.json_text(result) if response_format == 'markdown' else None,
    )


def create_special(
    identity: str, body: dict[str, Any], version: ApiVersion
) -> dict[str, Any]:
    """Send an Admin custom create with its complete official request body."""
    item = catalog.method('admin', identity, version)
    return render.entity(client.execute(item, {}, body), item, mutating=True)


@tool()
def ga_provision_account_ticket(
    body: dict[str, Any], *, api_version: ApiVersion = 'auto'
) -> dict[str, Any]:
    """Provision an account ticket; the user must complete Google's terms flow.

    Args:
        body: Official request with account and redirectUri.
        api_version: Reviewed default or explicit version.
    """
    item = catalog.method('admin', 'accounts.provisionAccountTicket', api_version)
    response = client.execute(item, {}, body)
    if ticket := response.get('accountTicketId'):
        response['terms_of_service_url'] = (
            'https://analytics.google.com/analytics/web/?provisioningSignup=false'
            f'#/termsofservice/{quote(ticket, safe="")}'
        )
        response['next_action'] = (
            'Open the terms URL as the same Google user to finish account creation.'
        )
    return render.entity(response, item, mutating=True)


@tool()
def ga_create_rollup_property(
    body: dict[str, Any], *, api_version: ApiVersion = 'auto'
) -> dict[str, Any]:
    """Create an Analytics 360 roll-up property.

    Args:
        body: Official request with rollupProperty and sourceProperties.
        api_version: Reviewed default or explicit version.
    """
    return create_special('properties.createRollupProperty', body, api_version)


@tool()
def ga_provision_subproperty(
    body: dict[str, Any], *, api_version: ApiVersion = 'auto'
) -> dict[str, Any]:
    """Provision an Analytics 360 subproperty with its filter and synchronization.

    Args:
        body: Complete ProvisionSubpropertyRequest from ga_describe_schema.
        api_version: Reviewed default or explicit version.
    """
    return create_special('properties.provisionSubproperty', body, api_version)


@tool()
def ga_reorder_event_edit_rules(
    parent: str,
    body: dict[str, Any],
    *,
    api_version: ApiVersion = 'auto',
) -> dict[str, Any]:
    """Change the evaluation order of a data stream's event-edit rules.

    Args:
        parent: Full properties/N/dataStreams/N name.
        body: Official body containing eventEditRules in the desired order.
        api_version: Reviewed default or explicit version.
    """
    item = catalog.method(
        'admin', 'properties.dataStreams.eventEditRules.reorder', api_version
    )
    return render.entity(
        client.execute(item, {'parent': parent}, body), item, mutating=True
    )


@tool()
def ga_submit_user_deletion(
    property: str,
    body: dict[str, Any],
    confirm: bool = False,
    *,
    api_version: ApiVersion = 'auto',
) -> dict[str, Any]:
    """Request irreversible deletion of one user's Analytics data.

    Args:
        property: properties/N or its numeric ID.
        body: Official deletion identifier; select exactly one supported identity.
        confirm: True only after user approval of the property and identity.
        api_version: Reviewed default or explicit version.
    """
    if (
        len(set(body) & {'userId', 'clientId', 'appInstanceId', 'userProvidedData'})
        != 1
    ):
        raise ToolError('Supply exactly one supported user-deletion identity.')
    item = catalog.method('admin', 'properties.submitUserDeletion', api_version)
    return render.entity(
        client.execute(
            item, {'name': names.property_name(property)}, body, confirm=confirm
        ),
        item,
        mutating=True,
    )


@tool()
def ga_acknowledge_user_data_collection(
    property: str,
    confirm: bool = False,
    *,
    api_version: ApiVersion = 'auto',
) -> dict[str, Any]:
    """Acknowledge Google's user-data collection terms for a property.

    Only call after the user approves the exact acknowledgement returned by
    ga_describe_schema('properties.acknowledgeUserDataCollection', api='admin').
    Args:
        property: properties/N or its numeric ID.
        confirm: True only after explicit approval of the legal acknowledgement.
        api_version: Reviewed default or explicit version.
    """
    item = catalog.method(
        'admin', 'properties.acknowledgeUserDataCollection', api_version
    )
    return render.entity(
        client.execute(
            item,
            {'property': names.property_name(property)},
            {'acknowledgement': ACKNOWLEDGEMENT},
            confirm=confirm,
        ),
        item,
        mutating=True,
    )


@tool()
def ga_review_dv360_link_proposal(
    name: str,
    operation: Literal['approve', 'cancel'],
    confirm: bool = False,
    *,
    api_version: ApiVersion = 'auto',
) -> dict[str, Any]:
    """Approve or cancel a Display & Video 360 advertiser link proposal.

    Args:
        name: Full proposal resource name copied from ga_list.
        operation: approve or cancel.
        confirm: True only after user approval of this proposal decision.
        api_version: Reviewed default or explicit version.
    """
    item, params = names.select(
        'ga_review_dv360_link_proposal',
        locator=name,
        action=operation,
        api_version=api_version,
    )
    return render.entity(
        client.execute(item, params, {}, confirm=confirm), item, mutating=True
    )
