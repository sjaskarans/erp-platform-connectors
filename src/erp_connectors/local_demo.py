"""Actual local SQL execution on synthetic data; not a Databricks emulator."""

import json
import re
import threading
import uuid
from datetime import datetime, timezone

import duckdb

from .demo_data import TABLES, configuration
from .models import ConnectorError, QueryResult


class LocalDemoConnector:
    def __init__(self):
        self.config = configuration("erp_demo", "erp_connector_demo_v1")
        self.lock = threading.Lock()
        self.db = duckdb.connect()
        self.db.execute("ATTACH ':memory:' AS erp_demo")
        self.db.execute('CREATE SCHEMA erp_demo.erp_connector_demo_v1')
        for name, spec in TABLES.items():
            columns = ", ".join(f'"{column}" {kind.replace("STRING", "VARCHAR")}' for column, kind in spec.columns)
            path = f'erp_demo.erp_connector_demo_v1."{name}"'
            self.db.execute(f"CREATE TABLE {path} ({columns})")
            placeholders = ",".join("?" for _ in spec.columns)
            self.db.executemany(f"INSERT INTO {path} VALUES ({placeholders})", spec.rows)

    def close(self):
        self.db.close()

    def check_connection(self):
        return self._run("connection-check", "SELECT 1 AS connected", {})

    def describe(self, object_id):
        spec = TABLES[object_id]
        # Fixture metadata is explicit; this is not Databricks information_schema.
        return QueryResult(
            platform="local-duckdb-demo", query_id=f"describe:{object_id}",
            statement_id=f"local-{uuid.uuid4()}",
            columns=[{"name": name, "type_name": "STRING"} for name in
                     ("column_name", "data_type", "full_data_type", "is_nullable", "ordinal_position", "comment")],
            rows=[[name, kind, kind, "YES", str(index), "Synthetic connector test field"]
                  for index, (name, kind) in enumerate(spec.columns, 1)],
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    def discover(self):
        return {name: self.describe(name) for name in TABLES}

    def preview(self, object_id, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Preview limit must be 1–100")
        obj = self.config["objects"][object_id]
        columns = ", ".join(f'"{name}"' for name in obj["preview_columns"])
        return self._run(f"preview:{object_id}",
                         f'SELECT {columns} FROM erp_demo.erp_connector_demo_v1."{object_id}" LIMIT {limit}', {})

    def execute(self, query_id, parameters):
        template = self.config["queries"][query_id]
        expected = {p["name"] for p in template["parameters"]}
        if set(parameters) != expected or any(v is not None and not isinstance(v, str) for v in parameters.values()):
            raise ValueError("Parameters must match the approved query")
        # Translate only trusted template syntax; values remain separately bound.
        sql = re.sub(r"`([^`]+)`", r'"\1"', template["statement"])
        for param in template["parameters"]:
            kind = "VARCHAR" if param["type"] == "STRING" else param["type"]
            sql = re.sub(rf':{re.escape(param["name"])}\b', f'CAST(${param["name"]} AS {kind})', sql)
        return self._run(query_id, sql, parameters)

    def _run(self, query_id, sql, parameters):
        try:
            with self.lock:
                cursor = self.db.execute(sql, parameters)
                columns = [{"name": c[0], "type_name": str(c[1])} for c in cursor.description]
                values = cursor.fetchmany(1001)
            rows = [[None if value is None else str(value) for value in row] for row in values]
            if len(rows) > 1000 or len(json.dumps(rows).encode()) > 4_000_000:
                raise ConnectorError("RESULT_LIMIT_EXCEEDED")
            return QueryResult(
                platform="local-duckdb-demo", query_id=query_id,
                statement_id=f"local-{uuid.uuid4()}", columns=columns, rows=rows,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                metadata={"synthetic": True},
            )
        except ConnectorError:
            raise
        except Exception:
            raise ConnectorError("LOCAL_QUERY_FAILED") from None
