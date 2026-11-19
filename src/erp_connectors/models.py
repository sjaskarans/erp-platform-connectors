from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, Protocol, TypedDict


class MetadataItem(TypedDict):
    name: str
    kind: NotRequired[Literal["TABLE", "VIEW"]]
    type: NotRequired[str]


class MetadataPage(TypedDict):
    items: list[MetadataItem]
    next_cursor: str | None
    prefix_case_sensitive: bool


@dataclass(frozen=True)
class Parameter:
    name: str
    type: str


@dataclass(frozen=True)
class QueryTemplate:
    """Trusted, operator-reviewed SELECT SQL. Never construct from user input."""

    statement: str
    parameters: tuple[Parameter, ...] = ()


@dataclass(frozen=True)
class DataObject:
    catalog: str
    schema: str
    name: str
    preview_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class QueryResult:
    platform: str
    query_id: str
    statement_id: str
    columns: list[dict[str, Any]]
    rows: list[list[str | None]]
    fetched_at: str
    truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class ConnectorError(RuntimeError):
    def __init__(self, code: str, statement_id: str | None = None):
        self.code = code
        self.statement_id = statement_id
        super().__init__(f"{code}; statement_id={statement_id or 'unavailable'}")


class Connector(Protocol):
    def check_connection(self) -> QueryResult: ...
    def describe(self, object_id: str) -> QueryResult: ...
    def preview(self, object_id: str, limit: int = 20) -> QueryResult: ...
    def execute(self, query_id: str, parameters: dict[str, str | None]) -> QueryResult: ...
