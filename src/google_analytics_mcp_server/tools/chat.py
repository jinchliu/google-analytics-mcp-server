"""Optional Analytics chat, with its own consent scope and session effects."""

from typing import Any

from google_analytics_mcp_server import catalog, client, contracts, names, render
from google_analytics_mcp_server.config import ApiVersion
from google_analytics_mcp_server.registry import tool


@tool(feature='ENABLE_CHAT')
def ga_chat(
    property: str,
    user_query: str | None = None,
    session_id: str | None = None,
    body: dict[str, Any] | None = None,
    *,
    api_version: ApiVersion = 'auto',
) -> dict[str, Any]:
    """Ask Analytics chat a question; creates or continues a server-side session.

    Requires GA_MCP_ENABLE_CHAT=1 and analytics.chatbot.read. Google's chat
    terms permit using chat activity to improve the product.
    Args:
        property: properties/N or its numeric ID.
        user_query: Question, or supply userQuery in body.
        session_id: Existing session ID to continue; omit to start a conversation.
        body: Complete official body, including returnPropertyQuota.
        api_version: auto or v1alpha for the pinned baseline.
    """
    item = catalog.method('data', 'properties.chat', api_version)
    payload = contracts.merge_body(body, userQuery=user_query, sessionId=session_id)
    payload.setdefault('returnPropertyQuota', True)
    return render.entity(
        client.execute(item, {'property': names.property_name(property)}, payload),
        item,
        mutating=True,
    )
