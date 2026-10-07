"""Measure actual text plus structured bytes for each output format."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from google_analytics_mcp_server import catalog, render


@pytest.mark.parametrize('fmt', ['markdown', 'json', 'json_full'])
def test_complete_report_fits_its_measured_budget(fmt, monkeypatch):
    item = catalog.method('data', 'properties.runReport')
    response = {
        'dimensionHeaders': [{'name': 'country'}],
        'metricHeaders': [{'name': 'activeUsers', 'type': 'TYPE_INTEGER'}],
        'rows': [
            {'dimensionValues': [{'value': '中国'}], 'metricValues': [{'value': '100'}]}
        ]
        * 20,
        'rowCount': 20,
    }
    result = render.report_result(item, {'limit': '20'}, response, fmt)
    size = sum(len(content.text.encode()) for content in result.content)
    if result.structured_content is not None:
        size += len(render.json_text(result.structured_content).encode())
    monkeypatch.setenv('GA_MCP_MAX_OUTPUT_BYTES', str(size))
    assert render.report_result(item, {'limit': '20'}, response, fmt) == result
    monkeypatch.setenv('GA_MCP_MAX_OUTPUT_BYTES', str(size - 1))
    with pytest.raises(ToolError, match='over GA_MCP_MAX_OUTPUT_BYTES'):
        render.report_result(item, {'limit': '20'}, response, fmt)
