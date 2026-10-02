import json

from tools.export_openapi import rendered_openapi


def test_openapi_contract_contains_only_versioned_api_routes():
    contract = json.loads(rendered_openapi())
    assert contract["openapi"].startswith("3.1")
    assert contract["paths"]
    assert all(path.startswith("/v1/") for path in contract["paths"])
    assert "/v1/health" in contract["paths"]
