"""Bounded Redshift Data API reads using an injected, IAM-authenticated AWS client."""

import base64
import hashlib
import json
import logging
import math
import re
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from .models import ConnectorError, DataObject, MetadataPage, QueryResult, QueryTemplate

LOG = logging.getLogger(__name__)


def quote_identifier(value: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 127 or any(ord(c) < 32 for c in value):
        raise ValueError("Use a valid Redshift identifier of at most 127 UTF-8 bytes")
    return '"' + value.replace('"', '""') + '"'


def parameter(value, kind):
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("Redshift Data API parameters must be nonempty strings; SQL NULL needs an explicit template")
    kind = kind.upper()
    if kind == "STRING":
        return value
    if kind == "INT":
        parsed = int(value)
        if not -(2 ** 63) <= parsed < 2 ** 63:
            raise ValueError("Integer exceeds BIGINT range")
        return str(parsed)
    if kind == "DECIMAL":
        try:
            parsed = Decimal(value)
            if not parsed.is_finite() or abs(parsed.adjusted()) > 1000:
                raise ValueError("Invalid finite decimal")
            return format(parsed, "f")
        except InvalidOperation:
            raise ValueError("Invalid decimal") from None
    if kind == "DATE":
        return date.fromisoformat(value).isoformat()
    if kind == "BOOLEAN" and value.lower() in {"true", "false"}:
        return value.lower()
    raise ValueError("Unsupported parameter type or value")


def cell(field, type_name):
    if not isinstance(field, dict) or len(field) != 1:
        raise ConnectorError("INVALID_RESULT_FIELD")
    kind, value = next(iter(field.items()))
    if kind == "isNull" and value is True:
        return None
    if kind == "stringValue" and isinstance(value, str):
        return value
    if kind == "booleanValue" and type(value) is bool:
        return "true" if value else "false"
    if kind == "longValue" and type(value) is int:
        return str(value)
    if kind == "blobValue" and isinstance(value, bytes):
        return base64.b64encode(value).decode()
    if kind == "doubleValue" and type(value) in {int, float} and math.isfinite(value):
        if type_name.lower().split("(")[0] in {"decimal", "numeric"}:
            raise ConnectorError("DECIMAL_PRECISION_UNSAFE")
        return str(value)
    raise ConnectorError("INVALID_RESULT_FIELD")


def aws_error_code(error):
    from botocore.exceptions import (ClientError, CredentialRetrievalError, EndpointConnectionError,
        NoCredentialsError, PartialCredentialsError, ProfileNotFound, ReadTimeoutError,
        ConnectTimeoutError, SSOTokenLoadError, UnauthorizedSSOTokenError)
    if isinstance(error, (NoCredentialsError, PartialCredentialsError, ProfileNotFound,
                          CredentialRetrievalError, SSOTokenLoadError, UnauthorizedSSOTokenError)):
        return "AWS_CREDENTIALS_UNAVAILABLE"
    if isinstance(error, (EndpointConnectionError, ReadTimeoutError, ConnectTimeoutError)):
        return "AWS_NETWORK_ERROR"
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "")
        if code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}:
            return "AWS_ACCESS_DENIED"
        if code in {"ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId", "UnrecognizedClientException"}:
            return "AWS_CREDENTIALS_UNAVAILABLE"
        if code in {"ResourceNotFoundException", "ClusterNotFound", "WorkgroupNotFoundException"}:
            return "REDSHIFT_TARGET_NOT_FOUND"
        if code in {"ThrottlingException", "TooManyRequestsException", "ActiveStatementsExceededException"}:
            return "AWS_THROTTLED"
        if code == "ValidationException":
            return "REDSHIFT_REQUEST_REJECTED"
    return "PLATFORM_REQUEST_FAILED"


class RedshiftConnector:
    def __init__(self, client, database: str, objects: dict[str, DataObject],
                 queries: dict[str, QueryTemplate] | None = None, *,
                 workgroup_name: str | None = None, cluster_identifier: str | None = None,
                 max_rows=1000, max_bytes=4_000_000, timeout_seconds=120, poll_seconds=1,
                 max_pages=1000, clock=time.monotonic, sleep=time.sleep):
        quote_identifier(database)
        if bool(workgroup_name) == bool(cluster_identifier):
            raise ValueError("Choose exactly one Redshift workgroup or provisioned cluster")
        target = workgroup_name or cluster_identifier
        pattern = r"[a-z0-9-]+" if workgroup_name else r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
        if not isinstance(target, str) or not re.fullmatch(pattern, target) or len(target) > 64:
            raise ValueError("Use the target name, not an endpoint or ARN")
        if cluster_identifier and len(cluster_identifier) > 63:
            raise ValueError("Cluster identifiers support at most 63 characters")
        if workgroup_name and len(workgroup_name) < 3:
            raise ValueError("Workgroup names require at least 3 characters")
        if not 1 <= max_rows <= 10_000 or not 1024 <= max_bytes <= 20_000_000:
            raise ValueError("Invalid result budgets")
        if timeout_seconds <= 0 or poll_seconds <= 0 or not 1 <= max_pages <= 1000:
            raise ValueError("Invalid timeout or pagination budget")
        self.client, self.database = client, database
        self.objects, self.queries = dict(objects), dict(queries or {})
        self.target = {"WorkgroupName": workgroup_name} if workgroup_name else {"ClusterIdentifier": cluster_identifier}
        self.max_rows, self.max_bytes, self.max_pages = max_rows, max_bytes, max_pages
        self.timeout_seconds, self.poll_seconds = timeout_seconds, poll_seconds
        self.clock, self.sleep = clock, sleep

    def close(self):
        self.client.close()

    def check_connection(self):
        return self._run("connection-check", "SELECT 1 AS connected, current_user AS user_name, current_database() AS database_name", {})

    def _object(self, object_id):
        obj = self.objects[object_id]
        if obj.catalog != self.database:
            raise ValueError("Use a separate profile for another Redshift database")
        return obj

    def describe(self, object_id):
        obj = self._object(object_id)
        return self._run(f"describe:{object_id}", """SELECT column_name, data_type,
            CASE WHEN LOWER(data_type) IN ('numeric', 'decimal') AND numeric_precision IS NOT NULL THEN data_type || '(' ||
                CAST(numeric_precision AS VARCHAR) || ',' || CAST(COALESCE(numeric_scale, 0) AS VARCHAR) || ')'
            WHEN character_maximum_length IS NOT NULL THEN data_type || '(' || CAST(character_maximum_length AS VARCHAR) || ')'
            ELSE data_type END AS full_data_type, is_nullable, ordinal_position
            FROM information_schema.columns WHERE table_catalog = :database_name
            AND table_schema = :schema_name AND table_name = :table_name ORDER BY ordinal_position""",
            {"database_name": self.database, "schema_name": obj.schema, "table_name": obj.name})

    def discover(self):
        return {name: self.describe(name) for name in self.objects}

    def preview(self, object_id, limit=20):
        obj = self._object(object_id)
        if not obj.preview_columns or type(limit) is not int or not 1 <= limit <= min(100, self.max_rows):
            raise ValueError("Choose approved preview columns and a limit between 1 and 100")
        columns = ", ".join(quote_identifier(c) for c in obj.preview_columns)
        return self._run(f"preview:{object_id}",
            f"SELECT {columns} FROM {quote_identifier(obj.schema)}.{quote_identifier(obj.name)} LIMIT {limit}", {})

    def execute(self, query_id, values):
        template = self.queries[query_id]
        if not isinstance(values, dict) or set(values) != {p.name for p in template.parameters}:
            raise ValueError("Provide exactly the registered query parameters")
        params = {}
        for spec in template.parameters:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", spec.name) or spec.name in params:
                raise ValueError("Invalid registered parameter definition")
            params[spec.name] = parameter(values[spec.name], spec.type)
        return self._run(query_id, template.statement, params)

    def browse(self, level: str, *, database="", schema_name="", table="", after="", prefix="") -> MetadataPage:
        if level not in {"databases", "schemas", "objects", "columns"}:
            raise ValueError("Unknown metadata level")
        for value in (database, schema_name, table, after, prefix):
            if not isinstance(value, str) or len(value.encode("utf-8")) > 127 or any(ord(c) < 32 for c in value):
                raise ValueError("Invalid metadata filter")
        if database and database != self.database:
            raise ValueError("Choose the database configured for this connection")
        if level == "databases":
            result = self._run("browse:databases", "SELECT current_database() AS database_name", {})
            if len(result.rows) != 1 or len(result.rows[0]) != 1 or result.rows[0][0] != self.database:
                raise ConnectorError("INVALID_METADATA_RESULT", result.statement_id)
            visible = self.database.startswith(prefix) and (not after or self.database > after)
            return {"items": [{"name": self.database}] if visible else [], "next_cursor": None, "prefix_case_sensitive": True}
        if not database or level in {"objects", "columns"} and not schema_name or level == "columns" and not table:
            raise ValueError("Choose a database, schema, and object as required")
        params, filters = {}, []
        if level == "schemas":
            field, source, selected = "schema_name", "information_schema.schemata", "schema_name"
            filters.append("schema_name NOT IN ('information_schema', 'pg_catalog')")
        elif level == "objects":
            field, source, selected = "table_name", "information_schema.tables", "table_name, table_type"
            filters.extend(["table_schema = :schema_name", "table_type IN ('BASE TABLE', 'VIEW')"])
            params["schema_name"] = schema_name
        else:
            field, source, selected = "column_name", "information_schema.columns", "column_name, data_type"
            filters.extend(["table_schema = :schema_name", "table_name = :table_name"])
            params.update(schema_name=schema_name, table_name=table)
        if after:
            filters.append(f"{field} > :after_name")
            params["after_name"] = after
        if prefix:
            filters.append(f"LEFT({field}, LEN(CAST(:name_prefix AS VARCHAR))) = CAST(:name_prefix AS VARCHAR)")
            params["name_prefix"] = prefix
        sql = f"SELECT {selected} FROM {source} WHERE {' AND '.join(filters)} ORDER BY {field} LIMIT 101"
        result = self._run("browse:" + level, sql, params)
        items = [{"name": row[0], **({"kind": "VIEW" if row[1] == "VIEW" else "TABLE"}
                 if level == "objects" else {"type": row[1]} if level == "columns" else {})} for row in result.rows]
        return {"items": items[:100], "next_cursor": items[99]["name"] if len(items) > 100 else None, "prefix_case_sensitive": True}

    def _run(self, query_id, sql, parameters):
        started = self.clock()
        statement_id = None
        terminal, outcome, cancellation = False, "FAILED", "not_requested"
        row_count = 0
        try:
            request = {**self.target, "Database": self.database, "Sql": sql,
                       "ClientToken": str(uuid.uuid4()), "ResultFormat": "JSON"}
            if parameters:
                request["Parameters"] = [{"name": name, "value": value} for name, value in parameters.items()]
            response = self.client.execute_statement(**request)
            statement_id = response.get("Id")
            if not isinstance(statement_id, str) or not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", statement_id):
                statement_id = None
                raise ConnectorError("MISSING_STATEMENT_ID")
            while True:
                if self.clock() - started >= self.timeout_seconds:
                    raise ConnectorError("TIMEOUT", statement_id)
                description = self.client.describe_statement(Id=statement_id)
                status = description.get("Status")
                if status in {"FINISHED", "FAILED", "ABORTED"}:
                    terminal = True
                    if status != "FINISHED":
                        raise ConnectorError("REDSHIFT_STATEMENT_FAILED", statement_id)
                    break
                if status not in {"SUBMITTED", "PICKED", "STARTED"}:
                    raise ConnectorError("INVALID_STATEMENT_STATUS", statement_id)
                self.sleep(min(self.poll_seconds, max(0, self.timeout_seconds - (self.clock() - started))))
            if description.get("HasResultSet") is not True:
                raise ConnectorError("MISSING_RESULT_SET", statement_id)
            expected = description.get("ResultRows")
            if expected == -1:
                expected = None  # Data API uses -1 when the row count is unknown.
            elif expected is not None and (type(expected) is not int or expected < 0):
                raise ConnectorError("RESULT_ROW_COUNT_MISMATCH", statement_id)
            if type(expected) is int and expected > self.max_rows:
                raise ConnectorError("RESULT_LIMIT_EXCEEDED", statement_id)
            if description.get("ResultSize", 0) > self.max_bytes:
                raise ConnectorError("RESULT_LIMIT_EXCEEDED", statement_id)
            rows, columns, byte_count = [], None, 0
            next_token, seen_tokens = None, set()
            for _ in range(self.max_pages):
                if self.clock() - started >= self.timeout_seconds:
                    raise ConnectorError("TIMEOUT", statement_id)
                page = self.client.get_statement_result(Id=statement_id, **({"NextToken": next_token} if next_token else {}))
                metadata = page.get("ColumnMetadata")
                if metadata:
                    normalized = [{"name": c.get("label") or c.get("name", ""), "type_name": c.get("typeName", "unknown"),
                                   "precision": c.get("precision"), "scale": c.get("scale"),
                                   "nullable": {0: False, 1: True}.get(c.get("nullable"))}
                                  for c in metadata]
                    if columns is not None and columns != normalized:
                        raise ConnectorError("INCONSISTENT_RESULT_METADATA", statement_id)
                    columns = normalized
                if not columns:
                    raise ConnectorError("MISSING_RESULT_METADATA", statement_id)
                total = page.get("TotalNumRows")
                if total is not None:
                    if type(total) is not int or total < 0 or expected is not None and total != expected:
                        raise ConnectorError("RESULT_ROW_COUNT_MISMATCH", statement_id)
                    expected = total
                batch = page.get("Records")
                if not isinstance(batch, list) or any(not isinstance(row, list) or len(row) != len(columns) for row in batch):
                    raise ConnectorError("INVALID_ROW_WIDTH", statement_id)
                serialized = [[cell(value, columns[i]["type_name"]) for i, value in enumerate(row)] for row in batch]
                byte_count += len(json.dumps(serialized, ensure_ascii=False).encode())
                if len(rows) + len(serialized) > self.max_rows or byte_count > self.max_bytes:
                    raise ConnectorError("RESULT_LIMIT_EXCEEDED", statement_id)
                rows.extend(serialized)
                next_token = page.get("NextToken")
                if not next_token:
                    break
                if not isinstance(next_token, str) or next_token in seen_tokens:
                    raise ConnectorError("INVALID_RESULT_PAGINATION", statement_id)
                seen_tokens.add(next_token)
            else:
                raise ConnectorError("RESULT_PAGE_LIMIT_EXCEEDED", statement_id)
            if expected is not None and len(rows) != expected:
                raise ConnectorError("RESULT_ROW_COUNT_MISMATCH", statement_id)
            row_count, outcome = len(rows), "SUCCEEDED"
            return QueryResult("redshift", query_id, statement_id, columns, rows, datetime.now(timezone.utc).isoformat(),
                               metadata={"sql_sha256": hashlib.sha256(sql.encode()).hexdigest()})
        except ConnectorError:
            raise
        except Exception as error:
            raise ConnectorError(aws_error_code(error), statement_id) from None
        finally:
            if statement_id and not terminal:
                try:
                    self.client.cancel_statement(Id=statement_id)
                    cancellation = "requested"
                except Exception:
                    cancellation = "request_failed"
            LOG.info(json.dumps({"event": "connector_query", "platform": "redshift", "query_id": query_id,
                "statement_id": statement_id, "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
                "outcome": outcome, "row_count": row_count, "cancellation": cancellation,
                "elapsed_seconds": round(self.clock() - started, 3)}))
