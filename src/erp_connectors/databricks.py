"""Bounded Databricks reads through the SQL Statement Execution API.

The query registry is a trusted deployment artifact. Database grants enforce
read-only access; this module is not a SQL sandbox or a semantic compiler.
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from .models import ConnectorError, DataObject, QueryResult, QueryTemplate

LOG = logging.getLogger(__name__)
BASE = "/api/2.0/sql/statements"


class Transport(Protocol):
    def do(self, method: str, path: str, **kwargs: Any) -> Any: ...


def quote_identifier(value: str) -> str:
    if not value or "\x00" in value:
        raise ValueError("Invalid SQL identifier")
    return "`" + value.replace("`", "``") + "`"


class DatabricksConnector:
    def __init__(
        self,
        transport: Transport,
        warehouse_id: str,
        objects: dict[str, DataObject],
        queries: dict[str, QueryTemplate] | None = None,
        *,
        max_rows: int = 1000,
        max_bytes: int = 4_000_000,
        timeout_seconds: float = 120,
        poll_seconds: float = 1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not warehouse_id or not 1 <= max_rows <= 10_000:
            raise ValueError("Warehouse ID and max_rows between 1 and 10000 required")
        if not 1024 <= max_bytes <= 20_000_000 or timeout_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("Invalid result budget or timeout")
        self.transport = transport
        self.warehouse_id = warehouse_id
        self.objects = dict(objects)
        self.queries = dict(queries or {})
        self.max_rows = max_rows
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.clock, self.sleep = clock, sleep

    def check_connection(self) -> QueryResult:
        return self._run("connection-check", "SELECT 1 AS connected", [])

    def describe(self, object_id: str) -> QueryResult:
        obj = self.objects[object_id]
        sql = f"""SELECT column_name, data_type, full_data_type, is_nullable,
                         ordinal_position, comment
                  FROM {quote_identifier(obj.catalog)}.information_schema.columns
                  WHERE table_schema = :schema_name AND table_name = :table_name
                  ORDER BY ordinal_position"""
        return self._run(f"describe:{object_id}", sql, [
            {"name": "schema_name", "type": "STRING", "value": obj.schema},
            {"name": "table_name", "type": "STRING", "value": obj.name},
        ])

    def discover(self) -> dict[str, QueryResult]:
        """Inspect only the approved objects; never crawl the whole account."""
        return {name: self.describe(name) for name in self.objects}

    def preview(self, object_id: str, limit: int = 20) -> QueryResult:
        obj = self.objects[object_id]
        if not obj.preview_columns:
            raise ValueError("Preview is disabled until columns are explicitly approved")
        if type(limit) is not int or not 1 <= limit <= min(100, self.max_rows):
            raise ValueError("Preview limit must be between 1 and min(100, max_rows)")
        columns = ", ".join(map(quote_identifier, obj.preview_columns))
        table = ".".join(map(quote_identifier, (obj.catalog, obj.schema, obj.name)))
        # LIMIT is validated as an integer; all identifiers come from trusted config.
        return self._run(f"preview:{object_id}", f"SELECT {columns} FROM {table} LIMIT {limit}", [])

    def execute(self, query_id: str, parameters: dict[str, str | None]) -> QueryResult:
        template = self.queries[query_id]
        names = [p.name for p in template.parameters]
        if len(names) != len(set(names)) or set(parameters) != set(names):
            raise ValueError("Parameters must exactly match the approved template")
        if any(v is not None and not isinstance(v, str) for v in parameters.values()):
            raise ValueError("Parameter values must be strings or null")
        bound = [{"name": p.name, "type": p.type, "value": parameters[p.name]}
                 for p in template.parameters]
        return self._run(query_id, template.statement, bound)

    def cancel(self, statement_id: str) -> None:
        """Request cancellation; success is not confirmation of terminal state."""
        # Statement IDs are opaque, but must remain a single URL path segment.
        from urllib.parse import quote
        self.transport.do("POST", f"{BASE}/{quote(statement_id, safe='')}/cancel")

    def _run(self, query_id: str, sql: str, parameters: list[dict]) -> QueryResult:
        started = self.clock()
        statement_id = None
        terminal = False
        outcome = "FAILED"
        row_count = 0
        cancellation = "not_requested"
        try:
            response = self.transport.do("POST", BASE, body={
                "warehouse_id": self.warehouse_id,
                "statement": sql,
                "parameters": parameters,
                "disposition": "INLINE",
                "format": "JSON_ARRAY",
                "wait_timeout": "0s",
                "row_limit": self.max_rows + 1,
                "byte_limit": self.max_bytes,
            })
            statement_id = response.get("statement_id")
            if not statement_id:
                raise ConnectorError("MISSING_STATEMENT_ID")
            from urllib.parse import quote
            path = f"{BASE}/{quote(statement_id, safe='')}"
            while True:
                state = response.get("status", {}).get("state")
                terminal = state in {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}
                if self.clock() - started >= self.timeout_seconds:
                    raise ConnectorError("TIMEOUT", statement_id)
                if state == "SUCCEEDED":
                    break
                if terminal:
                    # Platform messages may contain SQL, values, or private object names.
                    raise ConnectorError(f"STATEMENT_{state}", statement_id)
                if state not in {"PENDING", "RUNNING"}:
                    raise ConnectorError("INVALID_STATEMENT_STATE", statement_id)
                remaining = max(0, self.timeout_seconds - (self.clock() - started))
                self.sleep(min(self.poll_seconds, remaining))
                response = self.transport.do("GET", path)

            manifest = response.get("manifest")
            if not isinstance(manifest, dict) or "schema" not in manifest:
                raise ConnectorError("MISSING_MANIFEST", statement_id)
            if manifest.get("truncated"):
                raise ConnectorError("RESULT_LIMIT_EXCEEDED", statement_id)
            columns = manifest["schema"].get("columns", [])
            rows: list[list[str | None]] = []
            chunk = response.get("result") or {}
            seen: set[int] = {0}
            size = 0
            while True:
                if self.clock() - started >= self.timeout_seconds:
                    raise ConnectorError("TIMEOUT", statement_id)
                batch = chunk.get("data_array") or []
                if chunk.get("row_offset", len(rows)) != len(rows):
                    raise ConnectorError("INVALID_CHUNK_OFFSET", statement_id)
                if any(len(row) != len(columns) for row in batch):
                    raise ConnectorError("INVALID_ROW_WIDTH", statement_id)
                size += len(json.dumps(batch, ensure_ascii=False).encode("utf-8"))
                if len(rows) + len(batch) > self.max_rows or size > self.max_bytes:
                    raise ConnectorError("RESULT_LIMIT_EXCEEDED", statement_id)
                rows.extend(batch)
                next_index = chunk.get("next_chunk_index")
                if next_index is None:
                    break
                if type(next_index) is not int or next_index < 0 or next_index in seen:
                    raise ConnectorError("INVALID_CHUNK_SEQUENCE", statement_id)
                seen.add(next_index)
                # Construct the path ourselves; do not follow result-provided URLs.
                chunk = self.transport.do("GET", f"{path}/result/chunks/{next_index}")
            if manifest.get("total_row_count") != len(rows):
                raise ConnectorError("INCOMPLETE_RESULT", statement_id)
            row_count = len(rows)
            outcome = "SUCCEEDED"
            return QueryResult(
                platform="databricks", query_id=query_id, statement_id=statement_id,
                columns=columns, rows=rows,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                metadata={"warehouse_id": self.warehouse_id,
                          "sql_sha256": hashlib.sha256(sql.encode()).hexdigest()},
            )
        except ConnectorError:
            raise
        except Exception:
            # Submission failure may have an unknown outcome. Never auto-resubmit.
            raise ConnectorError("PLATFORM_REQUEST_FAILED", statement_id) from None
        finally:
            if statement_id and not terminal:
                try:
                    self.cancel(statement_id)
                    cancellation = "requested"
                except Exception:
                    cancellation = "request_failed"
            LOG.info(json.dumps({
                "event": "connector_query", "platform": "databricks",
                "query_id": query_id, "statement_id": statement_id,
                "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
                "outcome": outcome, "row_count": row_count,
                "elapsed_seconds": round(self.clock() - started, 3),
                "cancellation": cancellation,
            }))
