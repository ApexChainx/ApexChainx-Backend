"""Tests for the TxIndexer OpenAPI/Swagger documentation — Issue #710.

The issue asked for the indexer to be documented in the generated OpenAPI
schema, so the assertions here read the real ``app.openapi()`` document instead
of a copy: a route that loses its summary, its parameter descriptions or its
error responses fails this suite.
"""

from app.main import app

SCHEMA = app.openapi()
LIST_PATH = "/api/v1/indexer/transactions"
DETAIL_PATH = "/api/v1/indexer/transactions/{transaction_hash}"


def _operation(path: str) -> dict:
    return SCHEMA["paths"][path]["get"]


def _params(path: str) -> dict[str, dict]:
    return {param["name"]: param for param in _operation(path)["parameters"]}


class TestRoutesAreDocumented:
    def test_both_routes_are_registered(self):
        assert LIST_PATH in SCHEMA["paths"]
        assert DETAIL_PATH in SCHEMA["paths"]

    def test_routes_carry_a_summary_and_description(self):
        for path in (LIST_PATH, DETAIL_PATH):
            operation = _operation(path)
            assert operation["summary"].strip()
            assert operation["description"].strip()

    def test_routes_are_grouped_under_the_indexer_tag(self):
        for path in (LIST_PATH, DETAIL_PATH):
            assert _operation(path)["tags"] == ["indexer"]

    def test_tag_has_a_description_in_the_document(self):
        tags = {tag["name"]: tag for tag in SCHEMA.get("tags", [])}
        assert "indexer" in tags
        assert tags["indexer"]["description"].strip()

    def test_operation_ids_are_stable_for_client_generation(self):
        assert _operation(LIST_PATH)["operationId"] == "listIndexedTransactions"
        assert _operation(DETAIL_PATH)["operationId"] == "getIndexedTransaction"


class TestParametersAreDocumented:
    def test_every_query_parameter_has_a_description(self):
        query_params = {name: param for name, param in _params(LIST_PATH).items() if param["in"] == "query"}
        assert query_params
        for name, param in query_params.items():
            assert param.get("description"), f"{name} has no OpenAPI description"

    def test_limit_documents_its_bounds_and_default(self):
        limit = _params(LIST_PATH)["limit"]["schema"]
        assert limit["minimum"] == 1
        assert limit["maximum"] == 200
        assert limit["default"] == 50

    def test_offset_cannot_be_negative(self):
        offset = _params(LIST_PATH)["offset"]["schema"]
        assert offset["minimum"] == 0

    def test_account_filter_documents_that_it_matches_both_sides(self):
        description = _params(LIST_PATH)["account"]["description"]
        assert "from_address" in description
        assert "to_address" in description

    def test_stream_filters_are_documented(self):
        names = set(_params(LIST_PATH))
        assert {"transaction_hash", "account", "asset_code", "status", "type"} <= names
        assert {"created_after", "created_before"} <= names


class TestResponsesAreDocumented:
    def test_list_route_documents_its_error_statuses(self):
        responses = _operation(LIST_PATH)["responses"]
        assert {"200", "401", "403", "422"} <= set(responses)
        for status in ("200", "401", "403", "422"):
            assert responses[status]["description"].strip()

    def test_detail_route_documents_404(self):
        responses = _operation(DETAIL_PATH)["responses"]
        assert {"200", "401", "403", "404"} <= set(responses)
        assert "index" in responses["404"]["description"].lower()

    def test_list_response_is_a_documented_array_of_transactions(self):
        response = _operation(LIST_PATH)["responses"]["200"]
        schema_ref = response["content"]["application/json"]["schema"]
        assert schema_ref["items"]["$ref"].endswith("/IndexedTransactionResponse")

    def test_detail_response_is_a_single_transaction(self):
        response = _operation(DETAIL_PATH)["responses"]["200"]
        schema_ref = response["content"]["application/json"]["schema"]
        assert schema_ref["$ref"].endswith("/IndexedTransactionResponse")


class TestResponseModelIsDocumented:
    def _model(self) -> dict:
        return SCHEMA["components"]["schemas"]["IndexedTransactionResponse"]

    def test_every_field_is_described(self):
        for name, field in self._model()["properties"].items():
            assert field.get("description"), f"{name} has no OpenAPI description"

    def test_amount_documents_stroops(self):
        amount = self._model()["properties"]["amount"]
        assert "stroops" in amount["description"].lower()

    def test_model_ships_a_realistic_example(self):
        example = self._model()["example"]
        assert example["transaction_hash"]
        assert isinstance(example["amount"], int)
        assert example["status"] == "confirmed"


class TestSwaggerSurface:
    def test_schema_is_valid_openapi(self):
        assert SCHEMA["openapi"].startswith("3.")
        assert SCHEMA["info"]["title"]

    def test_indexer_paths_are_reachable_through_the_versioned_prefix(self):
        assert any(path.startswith("/api/v1/indexer") for path in SCHEMA["paths"])
