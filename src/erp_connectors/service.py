"""Local test connector service. Bind only to loopback; not a production API."""

import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from .models import ConnectorError
from .connections import ConnectionPayload, ConnectionStore, runtime_connector


class QueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    parameters: dict[str, str | None]


def create_app(connector=None):
    token = os.environ.get("ERP_API_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("Set ERP_API_TOKEN to a random token of at least 32 characters")
    mode = os.environ.get("ERP_CONNECTOR_MODE", "local-demo")
    if connector is None:
        if mode == "local-demo":
            from .local_demo import LocalDemoConnector
            connector = LocalDemoConnector()
        elif mode == "databricks":
            from .cli import load_connector
            connector = load_connector(os.environ.get("ERP_CONNECTOR_CONFIG", "connector.local.json"))
        elif mode == "snowflake":
            from .cli import load_snowflake_connector
            connector = load_snowflake_connector(os.environ.get("ERP_CONNECTOR_CONFIG", "connector.local.json"))
        else:
            raise RuntimeError("Unknown connector mode")

    @asynccontextmanager
    async def lifespan(app):
        yield
        close = getattr(connector, "close", None)
        if close:
            close()

    bearer = HTTPBearer(auto_error=False)

    def authorized(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not credentials or not secrets.compare_digest(credentials.credentials, token):
            raise HTTPException(401, "Invalid API token", headers={"WWW-Authenticate": "Bearer"})

    app = FastAPI(title="ERP connector test service", lifespan=lifespan,
                  dependencies=[Depends(authorized)])
    store = ConnectionStore(Path(os.environ.get("ERP_CONNECTION_DB", ".local-connections/profiles.sqlite3")))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Default validation details can echo a submitted secret back to clients.
        return JSONResponse(status_code=422, content={"detail": "Check the required fields and connection format"})

    @app.get("/v1/connections")
    def connections():
        return {"connections": store.list()}

    @app.post("/v1/connections", status_code=201)
    def create_connection(body: ConnectionPayload):
        try:
            return store.save(body)
        except ValueError:
            raise HTTPException(400, "Check the service credentials and private-key passphrase") from None
        except RuntimeError:
            raise HTTPException(503, "Windows secret protection is unavailable") from None

    @app.put("/v1/connections/{connection_id}")
    def update_connection(connection_id: str, body: ConnectionPayload):
        try:
            return store.save(body, connection_id)
        except KeyError:
            raise HTTPException(404, "Connection not found") from None
        except ValueError:
            raise HTTPException(400, "Check the credentials or create a separate profile for another platform") from None
        except RuntimeError:
            raise HTTPException(503, "Windows secret protection is unavailable") from None

    @app.delete("/v1/connections/{connection_id}")
    def delete_connection(connection_id: str):
        try:
            store.delete(connection_id)
            return {"deleted": True}
        except KeyError:
            raise HTTPException(404, "Connection not found") from None

    @app.post("/v1/connections/{connection_id}/test")
    def test_connection(connection_id: str):
        try:
            config, secret, version = store.runtime(connection_id)
        except KeyError:
            raise HTTPException(404, "Connection not found") from None
        except RuntimeError:
            raise HTTPException(503, "Windows secret protection is unavailable") from None
        error_code = None
        try:
            live = runtime_connector(config, secret)
            live.check_connection()
            for obj in config["objects"]:
                if not live.describe(obj["name"]).rows:
                    raise ConnectorError("OBJECT_NOT_VISIBLE")
        except ConnectorError as error:
            error_code = error.code
        except Exception:
            error_code = "AUTHENTICATION_OR_NETWORK_ERROR"
        try:
            return store.record_test(connection_id, version, error_code)
        except ValueError:
            raise HTTPException(409, "Connection changed; test the updated configuration") from None

    @app.get("/v1/connections/{connection_id}/objects/{object_id}/preview")
    def connection_preview(connection_id: str, object_id: str, limit: int = Query(5, ge=1, le=100)):
        try:
            config, secret, _ = store.runtime(connection_id)
            live = runtime_connector(config, secret)
        except KeyError:
            raise HTTPException(404, "Connection not found") from None
        except Exception:
            raise HTTPException(502, "Connection could not be opened") from None
        return invoke(live.preview, object_id, limit)

    def invoke(function, *args):
        try:
            return asdict(function(*args))
        except KeyError:
            raise HTTPException(404, "Unknown approved object or query") from None
        except ValueError:
            raise HTTPException(400, "Invalid request parameters") from None
        except ConnectorError as error:
            raise HTTPException(502, {"code": error.code, "statement_id": error.statement_id}) from None

    @app.get("/health")
    def health():
        return {"status": "ready", "mode": mode, "cloud_connectivity_checked": False}

    @app.get("/v1/check")
    def check():
        return invoke(connector.check_connection)

    @app.get("/v1/objects")
    def objects():
        registry = getattr(connector, "objects", None)
        if registry is None:
            registry = connector.config["objects"]
        return {"objects": list(registry)}

    @app.get("/v1/objects/{object_id}/schema")
    def describe(object_id: str):
        return invoke(connector.describe, object_id)

    @app.get("/v1/objects/{object_id}/preview")
    def preview(object_id: str, limit: int = Query(5, ge=1, le=100)):
        return invoke(connector.preview, object_id, limit)

    @app.post("/v1/queries/{query_id}")
    def query(query_id: str, body: QueryInput):
        return invoke(connector.execute, query_id, body.parameters)

    return app
