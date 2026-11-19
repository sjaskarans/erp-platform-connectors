import io
import json
import logging
import unittest
from datetime import date
from decimal import Decimal

from snowflake.connector.cursor import ResultMetadata

from erp_connectors.models import ConnectorError, DataObject, Parameter, QueryTemplate
from erp_connectors.snowflake import SnowflakeConnector, parameter, quote_identifier


class Cursor:
    def __init__(self, rows=None, error=None):
        self.rows = list(rows if rows is not None else [(Decimal("9007199254740993.123456"),)])
        self.error = error
        self.description = [ResultMetadata("AMOUNT", 0, None, None, 38, 6, True)]
        self.sfqid = "sf-query-1"
        self.calls, self.closed = [], False

    def execute_async(self, sql, params):
        self.calls.append((sql, params))
        if self.error:
            raise self.error

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def get_results_from_sfqid(self, query_id):
        self.result_query_id = query_id

    def fetchmany(self, count):
        result, self.rows = self.rows[:count], self.rows[count:]
        return result

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, cursor=None, states=None):
        self.primary = cursor or Cursor()
        self.cancel_cursor = Cursor([])
        self.cursor_count = 0
        self.states = list(states or ["SUCCESS"])
        self.closed = False

    def cursor(self):
        self.cursor_count += 1
        return self.primary if self.cursor_count == 1 else self.cancel_cursor

    def get_query_status_throw_if_error(self, query_id):
        value = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(value, Exception):
            raise value
        return value

    def is_still_running(self, status):
        return status == "RUNNING"

    def close(self):
        self.closed = True


def connector(connection, **kwargs):
    return SnowflakeConnector(lambda: connection, {
        "finance": DataObject("ERP_DEMO", "APPROVED", "FINANCE_LINES", ("AMOUNT",)),
        "no_preview": DataObject("ERP_DEMO", "APPROVED", "NO_PREVIEW"),
    }, {"total": QueryTemplate("SELECT SUM(AMOUNT) FROM ERP_DEMO.APPROVED.FINANCE_LINES WHERE COMPANY_CODE = ?",
                               (Parameter("company", "STRING"),))}, **kwargs)


class SnowflakeTests(unittest.TestCase):
    def test_exact_decimal_and_query_reference(self):
        conn = Connection()
        result = connector(conn).execute("total", {"company": "1000"})
        self.assertEqual(result.rows, [["9007199254740993.123456"]])
        self.assertEqual(result.columns[0]["scale"], 6)
        self.assertEqual(result.statement_id, "sf-query-1")
        self.assertEqual(result.platform, "snowflake")
        self.assertTrue(conn.closed and conn.primary.closed)

    def test_injection_is_bound_and_template_unchanged(self):
        conn = Connection()
        injected = "1000' OR 1=1 --"
        connector(conn).execute("total", {"company": injected})
        sql, params = conn.primary.calls[0]
        self.assertNotIn(injected, sql)
        self.assertEqual(params, (injected,))

    def test_wrong_query_or_parameters_do_not_open_connection(self):
        conn = Connection()
        client = connector(conn)
        with self.assertRaises(KeyError):
            client.execute("raw_sql", {})
        with self.assertRaises(ValueError):
            client.execute("total", {"company": "1", "sql": "DROP TABLE x"})
        self.assertEqual(conn.cursor_count, 0)

    def test_metadata_scoped_to_exact_database_schema_and_table(self):
        conn = Connection()
        connector(conn).describe("finance")
        sql, params = conn.primary.calls[0]
        self.assertIn('"ERP_DEMO".INFORMATION_SCHEMA.COLUMNS', sql)
        self.assertIn("NUMERIC_PRECISION", sql)
        self.assertEqual(params, ("APPROVED", "FINANCE_LINES"))

    def test_identifiers_quote_embedded_quotes(self):
        self.assertEqual(quote_identifier('a"b'), '"a""b"')

    def test_preview_limits_and_column_opt_in(self):
        conn = Connection()
        client = connector(conn)
        for limit in (True, 0, 101, "5; DROP TABLE x"):
            with self.assertRaises(ValueError):
                client.preview("finance", limit)
        with self.assertRaises(ValueError):
            client.preview("no_preview")
        client.preview("finance", 5)
        self.assertEqual(conn.primary.calls[0][0], 'SELECT "AMOUNT" FROM "ERP_DEMO"."APPROVED"."FINANCE_LINES" LIMIT 5')

    def test_row_limit_rejects_partial_results(self):
        with self.assertRaisesRegex(ConnectorError, "RESULT_LIMIT_EXCEEDED"):
            connector(Connection(Cursor([(1,), (2,)])), max_rows=1).check_connection()

    def test_byte_budget_rejects_large_cell(self):
        with self.assertRaisesRegex(ConnectorError, "RESULT_LIMIT_EXCEEDED"):
            connector(Connection(Cursor([("x" * 2000,)])), max_bytes=1024).check_connection()

    def test_null_and_empty_results(self):
        self.assertEqual(connector(Connection(Cursor([(None,)]))).check_connection().rows, [[None]])
        self.assertEqual(connector(Connection(Cursor([]))).check_connection().rows, [])

    def test_polling_and_timeout_cancel_with_bound_query_id(self):
        now = [0.0]
        conn = Connection(states=["RUNNING"])
        with self.assertRaisesRegex(ConnectorError, "TIMEOUT"):
            connector(conn, clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
                      timeout_seconds=1).check_connection()
        self.assertEqual(conn.cancel_cursor.calls, [("SELECT SYSTEM$CANCEL_QUERY(?)", ("sf-query-1",))])
        self.assertTrue(conn.closed and conn.cancel_cursor.closed)

    def test_error_redaction_and_cleanup(self):
        conn = Connection(Cursor(error=RuntimeError("SELECT private SQL; secret-key-value")))
        with self.assertRaises(ConnectorError) as caught:
            connector(conn).check_connection()
        self.assertEqual(caught.exception.code, "PLATFORM_REQUEST_FAILED")
        self.assertNotIn("secret-key-value", str(caught.exception))
        self.assertTrue(conn.closed and conn.primary.closed)

    def test_driver_errors_have_safe_actionable_categories(self):
        from snowflake.connector.errors import DatabaseError
        for number, state, expected in (
            (390144, "28000", "SNOWFLAKE_KEY_AUTHENTICATION_FAILED"),
            (394304, "28000", "SNOWFLAKE_KEY_AUTHENTICATION_FAILED"),
            (999999, "28000", "SNOWFLAKE_AUTHENTICATION_FAILED"),
            (2003, "42S02", "SNOWFLAKE_OBJECT_ACCESS_FAILED"),
            (999999, "XXXXX", "PLATFORM_REQUEST_FAILED"),
        ):
            with self.subTest(number=number):
                error = DatabaseError(msg="private-sql-and-key-material", errno=number, sqlstate=state)
                conn = Connection(Cursor(error=error))
                with self.assertRaises(ConnectorError) as caught:
                    connector(conn).check_connection()
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn("private-sql-and-key-material", str(caught.exception))
                self.assertTrue(conn.closed and conn.primary.closed)

    def test_audit_never_contains_bindings_results_or_sql(self):
        stream, logger = io.StringIO(), logging.getLogger("erp_connectors.snowflake")
        handler, previous = logging.StreamHandler(stream), logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            connector(Connection(Cursor([("sensitive-result",)]))).execute("total", {"company": "sensitive-company"})
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        entry = stream.getvalue()
        for value in ("sensitive-result", "sensitive-company", "SELECT SUM"):
            self.assertNotIn(value, entry)
        self.assertEqual(json.loads(entry)["outcome"], "SUCCEEDED")

    def test_typed_parameter_conversion(self):
        self.assertEqual(parameter("2026-11-18", "DATE"), date(2026, 11, 18))
        self.assertEqual(parameter("0.10", "DECIMAL"), Decimal("0.10"))
        self.assertEqual(parameter("false", "BOOLEAN"), False)
        for value, kind in (("NaN", "DECIMAL"), ("bad-date", "DATE"), ("no", "BOOLEAN")):
            with self.assertRaises(ValueError):
                parameter(value, kind)
