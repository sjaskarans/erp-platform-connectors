"""Operator-owned local connection profiles. Secrets protected by Windows DPAPI."""

import ctypes
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .databricks import DatabricksConnector
from .models import DataObject


class ObjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    catalog: str = Field(min_length=1, max_length=128)
    schema_name: str = Field(min_length=1, max_length=128)
    table_name: str = Field(min_length=1, max_length=128)
    preview_columns: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("name", "catalog", "schema_name", "table_name")
    @classmethod
    def identifier(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid object name")
        return value.strip()

    @field_validator("preview_columns")
    @classmethod
    def columns(cls, values):
        if any(not v.strip() or len(v) > 128 or any(ord(c) < 32 for c in v) for v in values):
            raise ValueError("Invalid column name")
        return list(dict.fromkeys(v.strip() for v in values))


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=100)
    workspace_url: str = Field(max_length=255)
    warehouse_id: str = Field(pattern=r"^[a-fA-F0-9]{8,64}$")
    client_id: str = Field(max_length=36)
    client_secret: SecretStr | None = Field(default=None, min_length=8, max_length=4096)
    objects: list[ObjectInput] = Field(min_length=1, max_length=30)

    @field_validator("name")
    @classmethod
    def display_name(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid name")
        return value.strip()

    @field_validator("workspace_url")
    @classmethod
    def workspace(cls, value):
        parsed = urlparse(value)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or parsed.port not in {None, 443}
                or not re.fullmatch(r"[0-9]+\.[0-9]+\.gcp\.databricks\.com", parsed.hostname or "")):
            raise ValueError("Use the exact HTTPS Google Cloud Databricks workspace URL")
        return f"https://{parsed.hostname}"

    @field_validator("client_id")
    @classmethod
    def application_id(cls, value):
        return str(uuid.UUID(value))

    @field_validator("objects")
    @classmethod
    def unique_objects(cls, objects):
        if len({obj.name for obj in objects}) != len(objects):
            raise ValueError("Object labels must be unique")
        return objects


class WindowsVault:
    """Current Windows user and machine binding; fail closed on other systems."""

    def _transform(self, value: bytes, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise RuntimeError("Local secret storage requires Windows DPAPI")
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        source = Blob(len(value), buffer)
        output = Blob()
        library = ctypes.WinDLL("crypt32", use_last_error=True)
        function = library.CryptUnprotectData if decrypt else library.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        function.restype = wintypes.BOOL
        # CRYPTPROTECT_UI_FORBIDDEN, without LOCAL_MACHINE: current-user protection.
        if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
            raise RuntimeError("Windows secret protection failed")
        try:
            return ctypes.string_at(output.data, output.size)
        finally:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.LocalFree.argtypes = [ctypes.c_void_p]
            kernel.LocalFree.restype = ctypes.c_void_p
            kernel.LocalFree(output.data)

    def protect(self, secret: str) -> bytes:
        return self._transform(secret.encode(), False)

    def reveal(self, value: bytes) -> str:
        return self._transform(value, True).decode()


class ConnectionStore:
    def __init__(self, path: Path, vault=None):
        self.path = path
        self.vault = vault or WindowsVault()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        with self._database() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS connections (
                id TEXT PRIMARY KEY, config TEXT NOT NULL, secret BLOB NOT NULL,
                version INTEGER NOT NULL, status TEXT NOT NULL, checked_at TEXT,
                error_code TEXT, created_at TEXT NOT NULL)""")

    @contextmanager
    def _database(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _row(self, connection_id):
        with self._database() as db:
            row = db.execute("SELECT * FROM connections WHERE id=?", (connection_id,)).fetchone()
        if row is None:
            raise KeyError(connection_id)
        return row

    def public(self, row):
        return {"id": row["id"], **json.loads(row["config"]), "secret_configured": True,
                "version": row["version"], "status": row["status"],
                "checked_at": row["checked_at"], "error_code": row["error_code"]}

    def list(self):
        with self._database() as db:
            return [self.public(row) for row in db.execute("SELECT * FROM connections ORDER BY created_at")]

    def save(self, value: ConnectionInput, connection_id: str | None = None):
        with self.lock:
            existing = self._row(connection_id) if connection_id else None
            secret = (self.vault.protect(value.client_secret.get_secret_value()) if value.client_secret
                      else existing["secret"] if existing else None)
            if secret is None:
                raise ValueError("A client secret is required for a new connection")
            config = value.model_dump(exclude={"client_secret"})
            connection_id = connection_id or str(uuid.uuid4())
            with self._database() as db:
                db.execute("""INSERT INTO connections VALUES (?, ?, ?, ?, 'saved', NULL, NULL, ?)
                    ON CONFLICT(id) DO UPDATE SET config=excluded.config, secret=excluded.secret,
                    version=excluded.version, status='saved', checked_at=NULL, error_code=NULL""",
                           (connection_id, json.dumps(config), secret,
                            existing["version"] + 1 if existing else 1,
                            datetime.now(timezone.utc).isoformat()))
            return self.public(self._row(connection_id))

    def runtime(self, connection_id):
        row = self._row(connection_id)
        config = json.loads(row["config"])
        return config, self.vault.reveal(row["secret"]), row["version"]

    def record_test(self, connection_id, version, error_code=None):
        with self._database() as db:
            changed = db.execute("""UPDATE connections SET status=?, checked_at=?, error_code=?
                WHERE id=? AND version=?""", (
                    "failed" if error_code else "verified", datetime.now(timezone.utc).isoformat(),
                    error_code, connection_id, version)).rowcount
        if not changed:
            raise ValueError("Connection changed during its test; run the test again")
        return self.public(self._row(connection_id))

    def delete(self, connection_id):
        with self._database() as db:
            if not db.execute("DELETE FROM connections WHERE id=?", (connection_id,)).rowcount:
                raise KeyError(connection_id)


def runtime_connector(config, secret):
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.core import Config
    client = WorkspaceClient(config=Config(
        host=config["workspace_url"], client_id=config["client_id"], client_secret=secret,
        auth_type="oauth-m2m", http_timeout_seconds=15, retry_timeout_seconds=15,
    ))
    objects = {obj["name"]: DataObject(obj["catalog"], obj["schema_name"], obj["table_name"],
                                     tuple(obj["preview_columns"])) for obj in config["objects"]}
    return DatabricksConnector(client.api_client, config["warehouse_id"], objects)
