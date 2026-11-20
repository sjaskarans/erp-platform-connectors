"""Read-only SAP OData v2/v4 adapter. No RFC, actions, batches or raw filters.

The hosting application supplies an authenticated requests.Session (communication
user, mTLS, or its managed token provider) and an operator-approved service root.
"""

import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from defusedxml.ElementTree import fromstring

from .models import ConnectorError, DataObject, MetadataPage, QueryResult

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
PRIMITIVES = {"String", "Boolean", "Byte", "SByte", "Int16", "Int32", "Int64", "Decimal",
              "Double", "Single", "Guid", "Date", "DateTime", "DateTimeOffset", "Time", "TimeOfDay", "Duration", "Binary"}


def service_root(value: str) -> str:
    p = urlsplit(value)
    if (p.scheme != "https" or not p.hostname or p.username or p.password or p.query or p.fragment
            or not p.path.startswith("/") or any(c in value for c in ("%", "\\"))
            or any(ord(c) <= 32 for c in value) or any(s in {".", ".."} for s in p.path.split("/"))):
        raise ValueError("Use an exact HTTPS OData service root without query parameters")
    # Accessing port also rejects malformed authorities.
    p.port
    return value.rstrip("/") + "/"


def identifier(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid OData identifier")
    return value


@dataclass(frozen=True)
class ODataFilter:
    property: str
    operator: str
    parameter: str


@dataclass(frozen=True)
class ODataQueryTemplate:
    """Trusted registry entry: approved object label, projection and typed filters."""
    object_id: str
    select: tuple[str, ...]
    filters: tuple[ODataFilter, ...] = ()
    order_by: tuple[str, ...] = ()


class SAPConnector:
    def __init__(self, session: requests.Session, root: str, objects: dict[str, DataObject],
                 queries: dict[str, ODataQueryTemplate] | None = None, *, odata_version: str = "2",
                 max_rows: int = 10000, max_bytes: int = 8_000_000, max_pages: int = 100,
                 timeout_seconds: float = 120, owns_session: bool = False, sap_client: str | None = None):
        if odata_version not in {"2", "4"} or min(max_rows, max_bytes, max_pages, timeout_seconds) <= 0:
            raise ValueError("Invalid SAP limits or OData version")
        self.session, self.root, self.objects = session, service_root(root), dict(objects)
        self.queries, self.version = dict(queries or {}), odata_version
        self.max_rows, self.max_bytes, self.max_pages = max_rows, max_bytes, max_pages
        self.timeout_seconds, self.owns_session = timeout_seconds, owns_session
        if sap_client is not None and not re.fullmatch(r"[0-9]{3}", sap_client):
            raise ValueError("SAP client must be three digits")
        self.sap_client = sap_client
        self._entities = None

    def close(self):
        if self.owns_session:
            self.session.close()

    def _get(self, url, *, params=None, xml=False, deadline, budget):
        if self.sap_client:
            existing = parse_qs(urlsplit(url).query).get("sap-client")
            if existing is not None and existing != [self.sap_client]:
                raise ConnectorError("SAP_UNSAFE_NEXT_LINK")
            if existing is None:
                params = {**(params or {}), "sap-client": self.sap_client}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ConnectorError("SAP_TIMEOUT")
        try:
            with self.session.get(url, params=params, headers={"Accept": "application/xml" if xml else
                                  ("application/json;IEEE754Compatible=true" if self.version == "4" else "application/json")},
                                  timeout=(min(5, remaining), min(15, remaining)),
                                  allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    code = {401: "SAP_AUTHENTICATION_FAILED", 403: "SAP_ACCESS_DENIED", 404: "SAP_SERVICE_NOT_FOUND",
                            429: "SAP_THROTTLED"}.get(response.status_code, "SAP_REQUEST_FAILED")
                    raise ConnectorError(code)
                content = bytearray()
                for chunk in response.iter_content(65536):
                    budget[0] += len(chunk)
                    if budget[0] > self.max_bytes:
                        raise ConnectorError("RESULT_BYTE_LIMIT")
                    if time.monotonic() > deadline:
                        raise ConnectorError("SAP_TIMEOUT")
                    content.extend(chunk)
                return bytes(content)
        except requests.RequestException:
            raise ConnectorError("SAP_NETWORK_ERROR") from None

    def _metadata(self):
        if self._entities is not None:
            return self._entities
        raw = self._get(self.root + "$metadata", xml=True, deadline=time.monotonic() + self.timeout_seconds, budget=[0])
        try:
            tree = fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
            schemas = [e for e in tree.iter() if e.tag.split("}")[-1] == "Schema"]
            expected_ns = "http://docs.oasis-open.org/odata/ns/edm" if self.version == "4" else "http://schemas.microsoft.com/ado/2008/09/edm"
            if not schemas or any(e.tag != "{" + expected_ns + "}Schema" for e in schemas):
                raise ValueError("OData version mismatch")
            types, aliases, entities = {}, {}, {}
            for schema in schemas:
                ns = schema.attrib["Namespace"]
                if schema.get("Alias"):
                    aliases[schema.get("Alias")] = ns
                for typ in schema:
                    if typ.tag.split("}")[-1] == "EntityType":
                        types[ns + "." + typ.attrib["Name"]] = typ
            def resolve(name):
                prefix, _, suffix = name.rpartition(".")
                return aliases.get(prefix, prefix) + "." + suffix
            def properties(name, seen):
                name = resolve(name)
                if name in seen or name not in types:
                    raise ValueError("Invalid inheritance")
                typ = types[name]
                props = properties(typ.get("BaseType"), seen | {name}) if typ.get("BaseType") else {}
                keys = {e.get("Name") for key in typ if key.tag.split("}")[-1] == "Key" for e in key}
                for prop in typ:
                    if prop.tag.split("}")[-1] == "Property" and prop.get("Type", "").removeprefix("Edm.") in PRIMITIVES:
                        pname = identifier(prop.attrib["Name"])
                        props[pname] = {"name": pname, "type_name": prop.attrib["Type"],
                                        "nullable": prop.get("Nullable", "true") != "false",
                                        "precision": prop.get("Precision"), "scale": prop.get("Scale"),
                                        "max_length": prop.get("MaxLength"), "is_key": pname in keys,
                                        "sap_attributes": {k.split("}")[-1]: v for k, v in prop.attrib.items()
                                                           if k.startswith("{http://www.sap.com/Protocols/SAPData}")},
                                        "annotations": [dict(a.attrib) for a in prop if a.tag.split("}")[-1] == "Annotation"]}
                return props
            for schema in schemas:
                for container in schema:
                    if container.tag.split("}")[-1] != "EntityContainer":
                        continue
                    for entity in container:
                        if entity.tag.split("}")[-1] != "EntitySet":
                            continue
                        name = identifier(entity.attrib["Name"])
                        if name in entities:
                            raise ValueError("Ambiguous entity set")
                        entities[name] = (schema.attrib["Namespace"], properties(entity.attrib["EntityType"], set()))
            if not entities:
                raise ValueError("No readable entity sets")
            self._entities = entities
            return entities
        except (ValueError, KeyError, TypeError, RecursionError):
            raise ConnectorError("SAP_METADATA_INVALID") from None
        except Exception:
            raise ConnectorError("SAP_METADATA_INVALID") from None

    @staticmethod
    def _result(query_id, columns, rows, **kwargs):
        return QueryResult("sap", query_id, str(uuid.uuid4()), columns, rows,
                           datetime.now(timezone.utc).isoformat(), **kwargs)

    def check_connection(self):
        entities = self._metadata()
        return self._result("check", [{"name": "entity_sets", "type_name": "Edm.Int32"}], [[str(len(entities))]],
                            metadata={"verification": "metadata_access_only", "odata_version": self.version})

    def _object(self, object_id):
        obj = self.objects[object_id]
        entities = self._metadata()
        if obj.catalog != "service" or obj.name not in entities or entities[obj.name][0] != obj.schema:
            raise ConnectorError("OBJECT_NOT_VISIBLE")
        return obj, entities[obj.name][1]

    def describe(self, object_id):
        _, props = self._object(object_id)
        return self._result("describe", [{"name": "name", "type_name": "string"}, {"name": "type", "type_name": "string"}],
                            [[p["name"], p["type_name"]] for p in props.values()], metadata={"properties": list(props.values())})

    def discover(self):
        return {name: self.describe(name) for name in self.objects}

    def browse(self, level, database="", schema_name="", table="", after="", prefix="") -> MetadataPage:
        entities = self._metadata()
        if level == "databases":
            items = [{"name": "service"}]
        elif database != "service":
            raise ValueError("Choose the configured service")
        elif level == "schemas":
            items = [{"name": ns} for ns in sorted({v[0] for v in entities.values()})]
        elif level == "objects":
            items = [{"name": n, "kind": "ENTITY"} for n, (ns, _) in entities.items() if ns == schema_name]
        elif level == "columns" and table in entities and entities[table][0] == schema_name:
            items = [{"name": n, "type": p["type_name"]} for n, p in entities[table][1].items()]
        else:
            raise ValueError("Choose a valid metadata level and entity")
        items = sorted((i for i in items if i["name"].startswith(prefix) and i["name"] > after), key=lambda i: i["name"])
        return {"items": items[:100], "next_cursor": items[99]["name"] if len(items) > 100 else None, "prefix_case_sensitive": True}

    def _literal(self, value, typ):
        if value is None:
            return "null"
        if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid parameter")
        if typ == "Edm.String":
            return "'" + value.replace("'", "''") + "'"
        if typ == "Edm.Boolean" and value in {"true", "false"}:
            return value
        if typ in {"Edm.Byte", "Edm.SByte", "Edm.Int16", "Edm.Int32", "Edm.Int64"}:
            if not re.fullmatch(r"-?\d{1,20}", value):
                raise ValueError("Invalid integer")
            bounds = {"Edm.Byte": (0, 255), "Edm.SByte": (-128, 127), "Edm.Int16": (-32768, 32767),
                      "Edm.Int32": (-2**31, 2**31-1), "Edm.Int64": (-2**63, 2**63-1)}[typ]
            if not bounds[0] <= int(value) <= bounds[1]:
                raise ValueError("Integer out of range")
            return str(int(value)) + ("L" if typ == "Edm.Int64" and self.version == "2" else "")
        if typ == "Edm.Decimal":
            if not re.fullmatch(r"-?\d{1,38}(?:\.\d{1,38})?", value):
                raise ValueError("Invalid decimal")
            return value + ("M" if self.version == "2" else "")
        if typ == "Edm.Guid":
            value = str(uuid.UUID(value))
            return "guid'" + value + "'" if self.version == "2" else value
        if typ == "Edm.Date" and self.version == "4":
            return date.fromisoformat(value).isoformat()
        raise ValueError("Unsupported filter parameter type")

    def execute(self, query_id, parameters):
        query = self.queries[query_id]
        if set(parameters) != {f.parameter for f in query.filters}:
            raise ValueError("Parameters must match the registered query")
        _, props = self._object(query.object_id)
        filters = []
        for f in query.filters:
            if f.property not in props or f.operator not in {"eq", "ne", "gt", "ge", "lt", "le"}:
                raise ValueError("Invalid registered filter")
            if parameters[f.parameter] is None and f.operator not in {"eq", "ne"}:
                raise ValueError("Null requires eq/ne")
            filters.append(f"{identifier(f.property)} {f.operator} {self._literal(parameters[f.parameter], props[f.property]['type_name'])}")
        for name in query.order_by:
            if name not in props:
                raise ValueError("Invalid order property")
        return self._read(query_id, query.object_id, query.select, filters, query.order_by)

    def preview(self, object_id, limit=20):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Preview limit must be 1..100")
        return self._read("preview", object_id, self.objects[object_id].preview_columns, [], (), limit)

    def _next(self, link, entity_url, current):
        if not isinstance(link, str) or len(link) > 16384 or any(ord(c) <= 32 for c in link) or "\\" in link:
            raise ConnectorError("SAP_UNSAFE_NEXT_LINK")
        target = urljoin(current, link)
        p, base = urlsplit(target), urlsplit(entity_url)
        if (p.scheme, p.netloc, p.path) != (base.scheme, base.netloc, base.path) or p.fragment or p.username or p.password:
            raise ConnectorError("SAP_UNSAFE_NEXT_LINK")
        return target

    def _read(self, query_id, object_id, selected, filters, order, limit=None):
        obj, props = self._object(object_id)
        if not selected or len(set(selected)) != len(selected) or not set(selected).issubset(props):
            raise ValueError("Choose explicitly approved scalar properties")
        url = self.root + identifier(obj.name)
        params = {"$select": ",".join(selected)}
        if filters:
            params["$filter"] = " and ".join(filters)
        if order:
            params["$orderby"] = ",".join(order)
        if limit:
            params["$top"] = str(limit + 1)
        rows, seen, budget, deadline = [], set(), [0], time.monotonic() + self.timeout_seconds
        for _ in range(self.max_pages):
            if url in seen:
                raise ConnectorError("SAP_PAGINATION_LOOP")
            seen.add(url)
            raw = self._get(url, params=params, deadline=deadline, budget=budget)
            params = None
            try:
                payload = json.loads(raw, parse_float=Decimal, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                data = payload["d"] if self.version == "2" else payload
                batch = data["results" if self.version == "2" else "value"]
                link = data.get("__next" if self.version == "2" else "@odata.nextLink")
                if not isinstance(batch, list):
                    raise ValueError()
                for item in batch:
                    row = []
                    for name in selected:
                        value = item[name]
                        if isinstance(value, (dict, list)) or not (value is None or isinstance(value, (str, int, bool, Decimal))):
                            raise ValueError()
                        row.append(None if value is None else str(value).lower() if isinstance(value, bool) else str(value))
                    rows.append(row)
                    if len(rows) > self.max_rows:
                        raise ConnectorError("RESULT_ROW_LIMIT")
                    if limit and len(rows) > limit:
                        return self._result(query_id, [props[n] for n in selected], rows[:limit], truncated=True)
            except (KeyError, TypeError, ValueError, InvalidOperation):
                raise ConnectorError("SAP_RESPONSE_INVALID") from None
            if not link:
                return self._result(query_id, [props[n] for n in selected], rows)
            url = self._next(link, self.root + obj.name, url)
        raise ConnectorError("RESULT_PAGE_LIMIT")
