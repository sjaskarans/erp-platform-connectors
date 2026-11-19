"""Separate local API gateway forwarding only the explicit connector routes."""

import os
import secrets

import requests
from . import __version__
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .service import QueryInput
from .connections import BrowseInput, ObjectSelectionInput, ConnectionPayload, connection_request_body


def create_app():
    token = os.environ.get("ERP_API_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("Set a random ERP_API_TOKEN of at least 32 characters")
    port = int(os.environ.get("ERP_CONNECTOR_PORT", "8001"))
    bearer = HTTPBearer(auto_error=False)

    def authorized(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not credentials or not secrets.compare_digest(credentials.credentials, token):
            raise HTTPException(401, "Invalid API token", headers={"WWW-Authenticate": "Bearer"})

    app = FastAPI(title="ERP connector reference gateway", version=__version__, dependencies=[Depends(authorized)])

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse(status_code=422, content={"detail": "Check the required fields and connection format"})

    def forward(method, path, body=None):
        try:
            response = requests.request(method, f"http://127.0.0.1:{port}{path}",
                                        headers={"Authorization": f"Bearer {token}"},
                                        json=body, timeout=180, proxies={"http": "", "https": ""})
            return JSONResponse(status_code=response.status_code, content=response.json())
        except (requests.RequestException, ValueError):
            raise HTTPException(502, "Connector service unavailable") from None

    @app.get("/health")
    def health():
        return forward("GET", "/health")

    @app.get("/v1/check")
    def check():
        return forward("GET", "/v1/check")

    @app.get("/v1/objects")
    def objects():
        return forward("GET", "/v1/objects")

    @app.get("/v1/objects/{object_id}/schema")
    def describe(object_id: str):
        from urllib.parse import quote
        return forward("GET", f"/v1/objects/{quote(object_id, safe='')}/schema")

    @app.get("/v1/objects/{object_id}/preview")
    def preview(object_id: str, limit: int = Query(5, ge=1, le=100)):
        from urllib.parse import quote
        return forward("GET", f"/v1/objects/{quote(object_id, safe='')}/preview?limit={limit}")

    @app.post("/v1/queries/{query_id}")
    def query(query_id: str, body: QueryInput):
        from urllib.parse import quote
        return forward("POST", f"/v1/queries/{quote(query_id, safe='')}", body.model_dump())

    @app.get("/v1/connections")
    def connections():
        return forward("GET", "/v1/connections")

    def connection_body(body):
        return connection_request_body(body)

    @app.post("/v1/connections", status_code=201)
    def create_connection(body: ConnectionPayload):
        return forward("POST", "/v1/connections", connection_body(body))

    @app.put("/v1/connections/{connection_id}")
    def update_connection(connection_id: str, body: ConnectionPayload):
        from urllib.parse import quote
        return forward("PUT", f"/v1/connections/{quote(connection_id, safe='')}", connection_body(body))

    @app.delete("/v1/connections/{connection_id}")
    def delete_connection(connection_id: str):
        from urllib.parse import quote
        return forward("DELETE", f"/v1/connections/{quote(connection_id, safe='')}")

    @app.post("/v1/connections/{connection_id}/test")
    def test_connection(connection_id: str):
        from urllib.parse import quote
        return forward("POST", f"/v1/connections/{quote(connection_id, safe='')}/test")

    @app.get("/v1/connections/{connection_id}/setup")
    def connection_setup(connection_id: str):
        from urllib.parse import quote
        return forward("GET", f"/v1/connections/{quote(connection_id, safe='')}/setup")

    @app.post("/v1/connections/{connection_id}/browse")
    def browse_connection(connection_id: str, body: BrowseInput):
        from urllib.parse import quote
        return forward("POST", f"/v1/connections/{quote(connection_id, safe='')}/browse", body.model_dump())

    @app.post("/v1/connections/{connection_id}/selection")
    def select_connection_objects(connection_id: str, body: ObjectSelectionInput):
        from urllib.parse import quote
        return forward("POST", f"/v1/connections/{quote(connection_id, safe='')}/selection", body.model_dump())

    @app.get("/v1/connections/{connection_id}/objects/{object_id}/preview")
    def connection_preview(connection_id: str, object_id: str, limit: int = Query(5, ge=1, le=100)):
        from urllib.parse import quote
        return forward("GET", f"/v1/connections/{quote(connection_id, safe='')}/objects/{quote(object_id, safe='')}/preview?limit={limit}")

    return app
