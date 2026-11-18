import io
import json
import logging
import unittest

from erp_connectors.databricks import BASE, DatabricksConnector, quote_identifier
from erp_connectors.models import ConnectorError, DataObject, Parameter, QueryTemplate


def success(rows=None, total=None, truncated=False, next_chunk=None):
    rows = [["1"]] if rows is None else rows
    result = {"data_array": rows, "row_offset": 0}
    if next_chunk is not None:
        result["next_chunk_index"] = next_chunk
    return {
        "statement_id": "s1", "status": {"state": "SUCCEEDED"},
        "manifest": {"schema": {"columns": [{"name": "amount", "type_name": "DECIMAL"}]},
                     "total_row_count": len(rows) if total is None else total,
                     "truncated": truncated},
        "result": result,
    }


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def do(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.endswith("/cancel"):
            return {}
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def connector(transport, **kwargs):
    return DatabricksConnector(
        transport, "warehouse", {
            "finance": DataObject("erp", "approved", "finance", ("amount",)),
            "no_preview": DataObject("erp", "approved", "restricted"),
        }, {
            "approved_total": QueryTemplate(
                "SELECT SUM(amount) FROM erp.approved.finance WHERE company_code = :company",
                (Parameter("company", "STRING"),),
            )
        }, **kwargs,
    )


class DatabricksTests(unittest.TestCase):
    def test_parameter_injection_is_bound_not_interpolated(self):
        transport = FakeTransport(success())
        value = "1000'; DROP TABLE finance; --"
        result = connector(transport).execute("approved_total", {"company": value})
        body = transport.calls[0][2]["body"]
        self.assertNotIn(value, body["statement"])
        self.assertEqual(body["parameters"][0]["value"], value)
        self.assertEqual(result.statement_id, "s1")

    def test_unknown_query_and_wrong_parameters_never_submit(self):
        transport = FakeTransport()
        client = connector(transport)
        with self.assertRaises(KeyError):
            client.execute("unapproved", {})
        for values in ({}, {"company": "1", "extra": "x"}, {"company": 1}):
            with self.assertRaises(ValueError):
                client.execute("approved_total", values)
        self.assertEqual(transport.calls, [])

    def test_preview_requires_approved_columns_and_bounded_integer(self):
        client = connector(FakeTransport())
        with self.assertRaises(ValueError):
            client.preview("no_preview")
        for limit in (0, 101, True, "1; DROP TABLE x"):
            with self.assertRaises(ValueError):
                client.preview("finance", limit)

    def test_metadata_is_scoped_and_parameterized(self):
        transport = FakeTransport(success())
        connector(transport).describe("finance")
        body = transport.calls[0][2]["body"]
        self.assertIn("`erp`.information_schema.columns", body["statement"])
        self.assertEqual(body["parameters"][0]["value"], "approved")
        self.assertEqual(body["parameters"][1]["value"], "finance")

    def test_identifier_quotes_are_escaped(self):
        self.assertEqual(quote_identifier("a`b"), "`a``b`")

    def test_async_polling_and_chunk_retrieval_preserve_decimal_null(self):
        transport = FakeTransport(
            {"statement_id": "s1", "status": {"state": "PENDING"}},
            success([["9007199254740993.123456"]], total=2, next_chunk=1),
            {"row_offset": 1, "data_array": [[None]]},
        )
        result = connector(transport, sleep=lambda _: None).check_connection()
        self.assertEqual(result.rows, [["9007199254740993.123456"], [None]])
        self.assertEqual(transport.calls[2][1], f"{BASE}/s1/result/chunks/1")

    def test_empty_results_are_valid(self):
        self.assertEqual(connector(FakeTransport(success([]))).check_connection().rows, [])

    def test_server_truncation_is_an_error(self):
        with self.assertRaisesRegex(ConnectorError, "RESULT_LIMIT_EXCEEDED"):
            connector(FakeTransport(success(truncated=True))).check_connection()

    def test_client_row_budget_is_enforced(self):
        with self.assertRaisesRegex(ConnectorError, "RESULT_LIMIT_EXCEEDED"):
            connector(FakeTransport(success([["1"], ["2"]])), max_rows=1).check_connection()

    def test_client_byte_budget_is_enforced(self):
        with self.assertRaisesRegex(ConnectorError, "RESULT_LIMIT_EXCEEDED"):
            connector(FakeTransport(success([["x" * 2000]])), max_bytes=1024).check_connection()

    def test_missing_rows_fail_instead_of_returning_partial_results(self):
        with self.assertRaisesRegex(ConnectorError, "INCOMPLETE_RESULT"):
            connector(FakeTransport(success(total=2))).check_connection()

    def test_duplicate_chunk_is_rejected(self):
        transport = FakeTransport(success(total=3, next_chunk=1),
                                  {"row_offset": 1, "data_array": [["2"]], "next_chunk_index": 1})
        with self.assertRaisesRegex(ConnectorError, "INVALID_CHUNK_SEQUENCE"):
            connector(transport).check_connection()

    def test_chunk_offset_is_checked(self):
        transport = FakeTransport(success(total=2, next_chunk=1),
                                  {"row_offset": 9, "data_array": [["2"]]})
        with self.assertRaisesRegex(ConnectorError, "INVALID_CHUNK_OFFSET"):
            connector(transport).check_connection()

    def test_timeout_requests_cancellation(self):
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        transport = FakeTransport(
            {"statement_id": "s1", "status": {"state": "RUNNING"}},
            {"statement_id": "s1", "status": {"state": "RUNNING"}},
        )
        with self.assertRaisesRegex(ConnectorError, "TIMEOUT"):
            connector(transport, clock=lambda: now[0], sleep=sleep, timeout_seconds=1).check_connection()
        self.assertTrue(transport.calls[-1][1].endswith("/cancel"))

    def test_poll_error_requests_cancellation_without_leaking_error(self):
        transport = FakeTransport(
            {"statement_id": "s1", "status": {"state": "RUNNING"}},
            RuntimeError("secret SQL customer=private"),
        )
        with self.assertRaises(ConnectorError) as caught:
            connector(transport, sleep=lambda _: None).check_connection()
        self.assertNotIn("secret", str(caught.exception))
        self.assertTrue(transport.calls[-1][1].endswith("/cancel"))

    def test_submission_failure_is_not_replayed_by_connector(self):
        transport = FakeTransport(RuntimeError("network"))
        with self.assertRaises(ConnectorError) as caught:
            connector(transport).check_connection()
        self.assertIsNone(caught.exception.statement_id)
        self.assertEqual(len(transport.calls), 1)

    def test_platform_failure_redacts_message(self):
        transport = FakeTransport({"statement_id": "s1", "status": {
            "state": "FAILED", "error": {"message": "SELECT sensitive_value"}}})
        with self.assertRaises(ConnectorError) as caught:
            connector(transport).check_connection()
        self.assertEqual(caught.exception.code, "STATEMENT_FAILED")
        self.assertEqual(len(transport.calls), 1)

    def test_audit_records_do_not_contain_parameter_values_or_rows(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("erp_connectors.databricks")
        old_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            connector(FakeTransport(success([["private-result"]]))).execute(
                "approved_total", {"company": "private-parameter"})
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
        audit = stream.getvalue()
        self.assertNotIn("private-result", audit)
        self.assertNotIn("private-parameter", audit)
        self.assertEqual(json.loads(audit)["outcome"], "SUCCEEDED")


if __name__ == "__main__":
    unittest.main()
