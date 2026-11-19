"""Snowflake reads with qmark binding, exact decimal results, and bounded fetches."""

import base64
import hashlib
import json
import logging
import time
from datetime import date, datetime, timezone
from decimal import Decimal, DecimalException

from .models import ConnectorError, DataObject, MetadataPage, QueryResult, QueryTemplate

LOG = logging.getLogger(__name__)


def quote_identifier(value: str) -> str:
    if not value or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid Snowflake identifier")
    return '"' + value.replace('"', '""') + '"'


def parameter(value, kind):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Parameters must be strings or null")
    kind = kind.upper()
    if kind == "STRING":
        return value
    if kind == "DATE":
        return date.fromisoformat(value)
    if kind == "INT":
        return int(value)
    if kind == "DECIMAL":
        try:
            number = Decimal(value)
        except DecimalException:
            raise ValueError("Invalid decimal parameter") from None
        if not number.is_finite():
            raise ValueError("Decimal must be finite")
        return number
    if kind == "BOOLEAN" and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError("Unsupported parameter type or value")


def cell(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return str(value)


class SnowflakeConnector:
    def __init__(self, connection_factory, objects: dict[str, DataObject],
                 queries: dict[str, QueryTemplate] | None = None, *, max_rows=1000,
                 max_bytes=4_000_000, timeout_seconds=120, poll_seconds=1,
                 clock=time.monotonic, sleep=time.sleep):
        if not 1 <= max_rows <= 10_000 or not 1024 <= max_bytes <= 20_000_000:
            raise ValueError("Invalid result budgets")
        if timeout_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("Invalid timeout")
        self.connection_factory = connection_factory
        self.objects, self.queries = dict(objects), dict(queries or {})
        self.max_rows, self.max_bytes = max_rows, max_bytes
        self.timeout_seconds, self.poll_seconds = timeout_seconds, poll_seconds
        self.clock, self.sleep = clock, sleep

    def check_connection(self):
        return self._run("connection-check", "SELECT 1 AS CONNECTED, CURRENT_USER() AS USER_NAME, CURRENT_ROLE() AS ROLE_NAME, CURRENT_WAREHOUSE() AS WAREHOUSE_NAME", ())

    def describe(self, object_id):
        obj = self.objects[object_id]
        return self._run(f"describe:{object_id}", f"""SELECT COLUMN_NAME, DATA_TYPE,
            CASE WHEN NUMERIC_PRECISION IS NOT NULL THEN
              DATA_TYPE || '(' || NUMERIC_PRECISION || ',' || COALESCE(NUMERIC_SCALE, 0) || ')'
              WHEN CHARACTER_MAXIMUM_LENGTH IS NOT NULL THEN
              DATA_TYPE || '(' || CHARACTER_MAXIMUM_LENGTH || ')'
              ELSE DATA_TYPE END AS FULL_DATA_TYPE,
            IS_NULLABLE, ORDINAL_POSITION, COMMENT
            FROM {quote_identifier(obj.catalog)}.INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION""",
                         (obj.schema, obj.name))

    def discover(self):
        return {name: self.describe(name) for name in self.objects}

    def browse(self, level: str, *, database: str = "", schema_name: str = "", table: str = "",
               after: str = "", prefix: str = "") -> MetadataPage:
        """Paginated metadata only, under the configured service identity and role."""
        schema = schema_name
        if level not in {"databases", "schemas", "objects", "columns"}:
            raise ValueError("Unknown metadata level")
        for value in (database, schema, table, after, prefix):
            if not isinstance(value, str) or len(value) > 255 or any(ord(c) < 32 for c in value):
                raise ValueError("Invalid metadata filter")
        if level != "databases" and not database:
            raise ValueError("Choose a database")
        if level in {"objects", "columns"} and not schema:
            raise ValueError("Choose a schema")
        if level == "columns" and not table:
            raise ValueError("Choose a table or view")
        if level == "databases":
            def literal(value):
                return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
            sql = "SHOW TERSE DATABASES"
            if prefix:
                sql += " STARTS WITH " + literal(prefix)
            sql += " LIMIT 101"
            if after:
                sql += " FROM " + literal(after)
            result = self._run("browse:databases", sql, ())
            names = [c["name"].lower() for c in result.columns]
            if "name" not in names:
                raise ConnectorError("INVALID_METADATA_RESULT", result.statement_id)
            items = [{"name": row[names.index("name")]} for row in result.rows]
        else:
            info = quote_identifier(database) + ".INFORMATION_SCHEMA."
            if level == "schemas":
                sql = f"""SELECT SCHEMA_NAME FROM {info}SCHEMATA
                    WHERE SCHEMA_NAME <> 'INFORMATION_SCHEMA' AND SCHEMA_NAME > ?
                    AND STARTSWITH(SCHEMA_NAME, ?) ORDER BY SCHEMA_NAME LIMIT 101"""
                params = (after, prefix)
            elif level == "objects":
                sql = f"""SELECT TABLE_NAME, TABLE_TYPE FROM {info}TABLES
                    WHERE TABLE_SCHEMA = ? AND TABLE_NAME > ? AND STARTSWITH(TABLE_NAME, ?)
                    AND TABLE_TYPE IN ('BASE TABLE', 'VIEW') ORDER BY TABLE_NAME LIMIT 101"""
                params = (schema, after, prefix)
            else:
                sql = f"""SELECT COLUMN_NAME, DATA_TYPE FROM {info}COLUMNS
                    WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME > ?
                    AND STARTSWITH(COLUMN_NAME, ?) ORDER BY COLUMN_NAME LIMIT 101"""
                params = (schema, table, after, prefix)
            result = self._run("browse:" + level, sql, params)
            items = [{"name": row[0], **({"kind": "VIEW" if row[1] == "VIEW" else "TABLE"}
                      if level == "objects" else {"type": row[1]} if level == "columns" else {})}
                     for row in result.rows]
        return {"items": items[:100], "next_cursor": items[99]["name"] if len(items) > 100 else None,
                "prefix_case_sensitive": True}

    def preview(self, object_id, limit=20):
        obj = self.objects[object_id]
        if not obj.preview_columns:
            raise ValueError("Preview columns must be explicitly approved")
        if type(limit) is not int or not 1 <= limit <= min(100, self.max_rows):
            raise ValueError("Invalid preview limit")
        columns = ", ".join(map(quote_identifier, obj.preview_columns))
        table = ".".join(map(quote_identifier, (obj.catalog, obj.schema, obj.name)))
        return self._run(f"preview:{object_id}", f"SELECT {columns} FROM {table} LIMIT {limit}", ())

    def execute(self, query_id, parameters):
        template = self.queries[query_id]
        names = [p.name for p in template.parameters]
        if len(names) != len(set(names)) or set(parameters) != set(names):
            raise ValueError("Parameters must exactly match the reviewed template")
        bound = tuple(parameter(parameters[p.name], p.type) for p in template.parameters)
        return self._run(query_id, template.statement, bound)

    def _cancel(self, connection, statement_id):
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT SYSTEM$CANCEL_QUERY(?)", (statement_id,))
        finally:
            cursor.close()

    def _run(self, query_id, sql, parameters):
        from snowflake.connector.constants import FIELD_ID_TO_NAME
        started = self.clock()
        connection = cursor = None
        statement_id = None
        terminal = False
        outcome, cancellation = "FAILED", "not_requested"
        row_count = 0
        try:
            connection = self.connection_factory()
            cursor = connection.cursor()
            cursor.execute_async(sql, parameters)
            statement_id = cursor.sfqid
            if not statement_id:
                raise ConnectorError("MISSING_STATEMENT_ID")
            while True:
                if self.clock() - started >= self.timeout_seconds:
                    raise ConnectorError("TIMEOUT", statement_id)
                status = connection.get_query_status_throw_if_error(statement_id)
                if not connection.is_still_running(status):
                    terminal = True
                    break
                remaining = max(0, self.timeout_seconds - (self.clock() - started))
                self.sleep(min(self.poll_seconds, remaining))
            cursor.get_results_from_sfqid(statement_id)
            columns = None
            rows, byte_count = [], 0
            while True:
                if self.clock() - started >= self.timeout_seconds:
                    raise ConnectorError("TIMEOUT", statement_id)
                batch = cursor.fetchmany(min(256, self.max_rows + 1 - len(rows)))
                # The driver attaches asynchronous results lazily: description
                # may stay None until the first fetch, including empty results.
                if columns is None:
                    columns = [{"name": column.name,
                                "type_name": FIELD_ID_TO_NAME.get(column.type_code, str(column.type_code)),
                                "precision": column.precision, "scale": column.scale,
                                "nullable": column.is_nullable}
                               for column in cursor.description or []]
                if not batch:
                    break
                serialized = [[cell(value) for value in row] for row in batch]
                if any(len(row) != len(columns) for row in serialized):
                    raise ConnectorError("INVALID_ROW_WIDTH", statement_id)
                byte_count += len(json.dumps(serialized, ensure_ascii=False).encode())
                if len(rows) + len(batch) > self.max_rows or byte_count > self.max_bytes:
                    raise ConnectorError("RESULT_LIMIT_EXCEEDED", statement_id)
                rows.extend(serialized)
            row_count, outcome = len(rows), "SUCCEEDED"
            return QueryResult("snowflake", query_id, statement_id, columns, rows,
                               datetime.now(timezone.utc).isoformat(),
                               metadata={"sql_sha256": hashlib.sha256(sql.encode()).hexdigest()})
        except ConnectorError:
            raise
        except Exception as error:
            # Driver errors can include SQL, bindings, or private account details.
            code = "PLATFORM_REQUEST_FAILED"
            from snowflake.connector.errors import Error as SnowflakeError
            if isinstance(error, SnowflakeError):
                if error.errno in {390144, 394300, 394301, 394302, 394303, 394304, 394305, 394306, 394307}:
                    code = "SNOWFLAKE_KEY_AUTHENTICATION_FAILED"
                elif error.sqlstate == "28000":
                    code = "SNOWFLAKE_AUTHENTICATION_FAILED"
                elif error.errno in {2003, 3001}:
                    code = "SNOWFLAKE_OBJECT_ACCESS_FAILED"
            raise ConnectorError(code, statement_id) from None
        finally:
            if connection and statement_id and not terminal:
                try:
                    self._cancel(connection, statement_id)
                    cancellation = "requested"
                except Exception:
                    cancellation = "request_failed"
            for resource in (cursor, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass
            LOG.info(json.dumps({"event": "connector_query", "platform": "snowflake", "query_id": query_id,
                                 "statement_id": statement_id, "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
                                 "outcome": outcome, "row_count": row_count, "cancellation": cancellation,
                                 "elapsed_seconds": round(self.clock() - started, 3)}))
