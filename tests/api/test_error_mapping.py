from fastapi.testclient import TestClient

from transit_scholar.api import create_app
from transit_scholar.product.errors import (
    ProductConflictError,
    ProductNotFoundError,
    ProductPayloadTooLargeError,
    ProductValidationError,
    ProviderUnavailableError,
)


def test_typed_product_errors_use_stable_http_envelopes(project_tmp_path):
    app = create_app(data_root=project_tmp_path)

    def raise_not_found():
        raise ProductNotFoundError("SECRET password=abc private/current.json")

    def raise_conflict():
        raise ProductConflictError("SECRET password=abc private/current.json")

    def raise_validation():
        raise ProductValidationError("SECRET password=abc private/current.json")

    def raise_too_large():
        raise ProductPayloadTooLargeError("SECRET password=abc private/current.json")

    def raise_provider_unavailable():
        raise ProviderUnavailableError("SECRET password=abc private/current.json")

    app.add_api_route("/typed/not-found", raise_not_found)
    app.add_api_route("/typed/conflict", raise_conflict)
    app.add_api_route("/typed/validation", raise_validation)
    app.add_api_route("/typed/too-large", raise_too_large)
    app.add_api_route("/typed/provider", raise_provider_unavailable)

    with TestClient(app, raise_server_exceptions=False) as client:
        responses = [
            client.get("/typed/not-found"),
            client.get("/typed/conflict"),
            client.get("/typed/validation"),
            client.get("/typed/too-large"),
            client.get("/typed/provider"),
        ]

    for response, status_code, code in zip(
        responses,
        (404, 409, 422, 413, 503),
        ("NOT_FOUND", "CONFLICT", "VALIDATION_ERROR", "UPLOAD_TOO_LARGE", "PROVIDER_UNAVAILABLE"),
    ):
        for diagnostic in ("SECRET", "password=abc", "private/current.json"):
            assert diagnostic not in response.text
        assert response.status_code == status_code
        assert response.json()["error"]["code"] == code
        assert set(response.json()["error"]) == {"code", "message", "details"}


def test_unexpected_errors_are_internal_and_sanitized(project_tmp_path):
    app = create_app(data_root=project_tmp_path)

    def raise_unexpected():
        raise ValueError("private database diagnostic")

    app.add_api_route("/unexpected", raise_unexpected)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/unexpected")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "An unexpected internal error occurred",
            "details": {},
        }
    }
    assert "private database diagnostic" not in response.text
