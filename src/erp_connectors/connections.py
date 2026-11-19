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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .databricks import DatabricksConnector
from .models import DataObject


class ObjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    catalog: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(min_length=1, max_length=255)
    table_name: str = Field(min_length=1, max_length=255)
    preview_columns: list[str] = Field(default_factory=list, max_length=30)
    object_kind: Literal["TABLE", "VIEW"] = "TABLE"

    @field_validator("name", "catalog", "schema_name", "table_name")
    @classmethod
    def identifier(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid object name")
        return value.strip()

    @field_validator("preview_columns")
    @classmethod
    def columns(cls, values):
        if any(not v.strip() or len(v) > 255 or any(ord(c) < 32 for c in v) for v in values):
            raise ValueError("Invalid column name")
        return list(dict.fromkeys(v.strip() for v in values))


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    platform: Literal["databricks"] = "databricks"
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


class SnowflakeAccessScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    catalog: str = Field(min_length=1, max_length=255)
    schema_name: str = Field(min_length=1, max_length=255)
    _names = field_validator("catalog", "schema_name")(ObjectInput.identifier.__func__)


class ObjectSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1)
    objects: list[ObjectInput] = Field(max_length=30)
    _objects = field_validator("objects")(ConnectionInput.unique_objects.__func__)


class BrowseInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    level: Literal["databases", "schemas", "objects", "columns"]
    database: str = Field(default="", max_length=255)
    schema_name: str = Field(default="", max_length=255)
    table: str = Field(default="", max_length=255)
    after: str = Field(default="", max_length=255)
    prefix: str = Field(default="", max_length=255)


class SnowflakeConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    platform: Literal["snowflake"]
    name: str = Field(min_length=1, max_length=100)
    account: str = Field(min_length=3, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*[A-Za-z0-9]$")
    warehouse: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=255)
    private_key_pem: SecretStr | None = Field(default=None, max_length=16000)
    private_key_passphrase: SecretStr | None = Field(default=None, max_length=4096)
    objects: list[ObjectInput] = Field(default_factory=list, max_length=30)
    access_scope: SnowflakeAccessScope | None = None

    _name = field_validator("name")(ConnectionInput.display_name.__func__)
    _objects = field_validator("objects")(ConnectionInput.unique_objects.__func__)

    @field_validator("warehouse", "username", "role")
    @classmethod
    def identifier(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid Snowflake name")
        return value.strip()

    @field_validator("account")
    @classmethod
    def account_identifier(cls, value):
        if ".." in value or value.endswith(".snowflakecomputing.com"):
            raise ValueError("Use an account identifier, not a URL or hostname")
        return value


ConnectionPayload = ConnectionInput | SnowflakeConnectionInput
SECRET_FIELDS = {"client_secret", "private_key_pem", "private_key_passphrase"}


def private_key_der(pem: str, passphrase: str | None = None) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=passphrase.encode() if passphrase else None)
        if not isinstance(key, RSAPrivateKey) or key.key_size < 2048:
            raise ValueError("Use RSA with at least 2048 bits")
        return key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption())
    except Exception:
        raise ValueError("Invalid RSA private key or passphrase") from None


def connection_request_body(value: ConnectionPayload) -> dict:
    """Private wire payload for gateway→connector; never use as a public DTO."""
    result = value.model_dump(exclude=SECRET_FIELDS)
    for name in SECRET_FIELDS:
        secret = getattr(value, name, None)
        if secret is not None:
            result[name] = secret.get_secret_value()
    return result


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
        config = json.loads(row["config"])
        config.setdefault("platform", "databricks")
        return {"id": row["id"], **config, "secret_configured": True,
                "version": row["version"], "status": row["status"],
                "checked_at": row["checked_at"], "error_code": row["error_code"]}

    def list(self):
        with self._database() as db:
            return [self.public(row) for row in db.execute("SELECT * FROM connections ORDER BY created_at")]

    def save(self, value: ConnectionPayload, connection_id: str | None = None):
        with self.lock:
            existing = self._row(connection_id) if connection_id else None
            if existing and json.loads(existing["config"]).get("platform", "databricks") != value.platform:
                raise ValueError("Create a separate profile to change platforms")
            plaintext = None
            if isinstance(value, SnowflakeConnectionInput):
                if not existing and value.private_key_pem is None and value.private_key_passphrase is None:
                    from cryptography.hazmat.primitives import serialization
                    from cryptography.hazmat.primitives.asymmetric import rsa
                    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
                    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()).decode()
                    plaintext = json.dumps({"private_key_pem": pem, "private_key_passphrase": None})
                if value.private_key_pem is not None:
                    pem = value.private_key_pem.get_secret_value()
                    password = value.private_key_passphrase.get_secret_value() if value.private_key_passphrase else None
                    private_key_der(pem, password)
                    plaintext = json.dumps({"private_key_pem": pem, "private_key_passphrase": password})
                elif value.private_key_passphrase is not None:
                    raise ValueError("Provide the key when replacing its passphrase")
            elif value.client_secret:
                plaintext = value.client_secret.get_secret_value()
            secret = self.vault.protect(plaintext) if plaintext is not None else existing["secret"] if existing else None
            if secret is None:
                raise ValueError("Credentials are required for a new connection")
            config = value.model_dump(exclude=SECRET_FIELDS)
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

    def select_objects(self, connection_id, value: ObjectSelectionInput):
        """Versioned selection update that cannot replace identity or credentials."""
        with self.lock:
            row = self._row(connection_id)
            config = json.loads(row["config"])
            if config.get("platform") != "snowflake":
                raise ValueError("Discovery selection is available for Snowflake only")
            config["objects"] = [obj.model_dump() for obj in value.objects]
            with self._database() as db:
                changed = db.execute("""UPDATE connections SET config=?, version=version+1,
                    status='saved', checked_at=NULL, error_code=NULL WHERE id=? AND version=?""",
                    (json.dumps(config), connection_id, value.version)).rowcount
            if not changed:
                raise ValueError("Connection changed; reload before saving your selection")
            return self.public(self._row(connection_id))

    def snowflake_setup(self, connection_id):
        """Only public key material leaves the service. Reading setup never rotates keys."""
        import base64
        import hashlib
        from cryptography.hazmat.primitives import serialization
        config, secret, version = self.runtime(connection_id)
        if config.get("platform") != "snowflake":
            raise ValueError("Setup is available for Snowflake only")
        bundle = json.loads(secret)
        key = serialization.load_der_private_key(private_key_der(
            bundle["private_key_pem"], bundle.get("private_key_passphrase")), password=None)
        public = key.public_key().public_bytes(serialization.Encoding.DER,
                                               serialization.PublicFormat.SubjectPublicKeyInfo)
        public_key = base64.b64encode(public).decode()
        def ident(value):
            return '"' + value.replace('"', '""') + '"'
        def literal(value):
            return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
        role, user = ident(config["role"]), ident(config["username"])
        statements = [
            "-- Run once as an administrator authorized to create identities and grant source access.",
            "-- Use NEW dedicated user and role names. Stop on errors; do not overwrite an existing identity.",
            f"CREATE ROLE {role};",
            f"CREATE USER {user} TYPE = SERVICE DEFAULT_ROLE = {literal(config['role'])} "
            f"DEFAULT_WAREHOUSE = {literal(config['warehouse'])} RSA_PUBLIC_KEY = '{public_key}';",
            f"GRANT ROLE {role} TO USER {user};",
            f"GRANT ROLE {role} TO ROLE SYSADMIN;",
            f"GRANT USAGE ON WAREHOUSE {ident(config['warehouse'])} TO ROLE {role};",
        ]
        grants = []
        scope = config.get("access_scope")
        if scope:
            database = ident(scope["catalog"])
            schema = database + "." + ident(scope["schema_name"])
            grants.extend([
                "-- Administrator-approved schema scope: all CURRENT tables and views.",
                "-- No future grants. Selecting objects in the app does not narrow Snowflake privileges.",
                f"GRANT USAGE ON DATABASE {database} TO ROLE {role};",
                f"GRANT USAGE ON SCHEMA {schema} TO ROLE {role};",
                f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO ROLE {role};",
                f"GRANT SELECT ON ALL VIEWS IN SCHEMA {schema} TO ROLE {role};",
            ])
        for obj in config["objects"]:
            database = ident(obj["catalog"])
            schema = database + "." + ident(obj["schema_name"])
            grants.extend([
                f"GRANT USAGE ON DATABASE {database} TO ROLE {role};",
                f"GRANT USAGE ON SCHEMA {schema} TO ROLE {role};",
                f"GRANT SELECT ON {obj.get('object_kind', 'TABLE')} {schema}.{ident(obj['table_name'])} TO ROLE {role};",
            ])
        statements.extend(dict.fromkeys(grants))
        return {"connection_id": connection_id, "version": version, "public_key": public_key,
                "fingerprint": "SHA256:" + base64.b64encode(hashlib.sha256(public).digest()).decode(),
                "diagnostic_sql": f"DESCRIBE USER {user};",
                "sql": "\n".join(statements)}

    def record_test(self, connection_id, version, error_code=None, has_objects=True):
        with self._database() as db:
            changed = db.execute("""UPDATE connections SET status=?, checked_at=?, error_code=?
                WHERE id=? AND version=?""", (
                    "failed" if error_code else "verified" if has_objects else "connected", datetime.now(timezone.utc).isoformat(),
                    error_code, connection_id, version)).rowcount
        if not changed:
            raise ValueError("Connection changed during its test; run the test again")
        return self.public(self._row(connection_id))

    def delete(self, connection_id):
        with self._database() as db:
            if not db.execute("DELETE FROM connections WHERE id=?", (connection_id,)).rowcount:
                raise KeyError(connection_id)


def runtime_connector(config, secret):
    objects = {obj["name"]: DataObject(obj["catalog"], obj["schema_name"], obj["table_name"],
                                     tuple(obj["preview_columns"])) for obj in config["objects"]}
    if config.get("platform") == "snowflake":
        from .snowflake import SnowflakeConnector
        import snowflake.connector
        credentials = json.loads(secret)
        key = private_key_der(credentials["private_key_pem"], credentials.get("private_key_passphrase"))
        return SnowflakeConnector(lambda: snowflake.connector.connect(
            account=config["account"], user=config["username"], warehouse=config["warehouse"],
            role=config["role"], authenticator="SNOWFLAKE_JWT", private_key=key, paramstyle="qmark",
            login_timeout=15, network_timeout=15, socket_timeout=15, client_session_keep_alive=False,
            enable_server_session_keep_alive_auto_detection=False,
            session_parameters={"STATEMENT_TIMEOUT_IN_SECONDS": 120, "QUERY_TAG": "erp-platform-connectors",
                                "CLIENT_TELEMETRY_ENABLED": False},
        ), objects)
    if config.get("platform", "databricks") != "databricks":
        raise ValueError("Unknown connection platform")
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.core import Config
    client = WorkspaceClient(config=Config(
        host=config["workspace_url"], client_id=config["client_id"], client_secret=secret,
        auth_type="oauth-m2m", http_timeout_seconds=15, retry_timeout_seconds=15,
    ))
    return DatabricksConnector(client.api_client, config["warehouse_id"], objects)
