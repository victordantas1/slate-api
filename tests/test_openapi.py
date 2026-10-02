import json
import re
from typing import Any

import pytest
from sqlalchemy import CheckConstraint

from app.db.base import Base
from app.openapi import OPENAPI_PATH, render_openapi

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
ENUM_CHECK = re.compile(r"^(\w+) IN \((.+)\)$")
# Schemas sem coluna correspondente no banco, cujo campo só coincide no nome.
NOT_DB_BACKED = {"HealthResponse"}


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    result: dict[str, Any] = json.loads(render_openapi())
    return result


def test_published_openapi_matches_app() -> None:
    assert OPENAPI_PATH.read_text(encoding="utf-8") == render_openapi(), (
        "openapi.json desatualizado: rode `uv run python -m app.openapi` e commite o arquivo"
    )


def test_render_is_deterministic() -> None:
    assert render_openapi() == render_openapi()


def _operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method.upper(), path, operation)
        for path, item in schema["paths"].items()
        for method, operation in item.items()
        if method in HTTP_METHODS
    ]


def test_every_endpoint_has_typed_success_response(schema: dict[str, Any]) -> None:
    untyped = []
    for method, path, operation in _operations(schema):
        success = {code: r for code, r in operation["responses"].items() if code.startswith("2")}
        assert success, f"{method} {path} sem resposta 2xx documentada"
        for code, response in success.items():
            if code == "204":
                continue
            body = response.get("content", {}).get("application/json", {}).get("schema")
            # Rota sem response_model sai como `{}`: o slate-web geraria `unknown`.
            if not body:
                untyped.append(f"{method} {path} {code}")
    assert not untyped, f"respostas sem response_model tipado: {untyped}"


def _enum_columns() -> dict[str, list[set[str]]]:
    """Colunas com CHECK `<coluna> IN (...)`, mapeadas para os conjuntos de valores aceitos.

    O mesmo nome pode ter domínios diferentes em tabelas diferentes (`kind`, `status`).
    """
    columns: dict[str, list[set[str]]] = {}
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if not isinstance(constraint, CheckConstraint):
                continue
            match = ENUM_CHECK.match(str(constraint.sqltext))
            if match:
                values = {v.strip().strip("'") for v in match.group(2).split(",")}
                columns.setdefault(match.group(1), []).append(values)
    return columns


def _resolve(schema: dict[str, Any], node: dict[str, Any]) -> list[dict[str, Any]]:
    """Desdobra `$ref` e `anyOf` (campos opcionais) até os schemas concretos."""
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        return _resolve(schema, schema["components"]["schemas"][name])
    if "anyOf" in node:
        return [leaf for option in node["anyOf"] for leaf in _resolve(schema, option)]
    return [node]


def test_enum_columns_exist_in_models() -> None:
    # Guarda contra o regex parar de casar silenciosamente e o teste abaixo virar no-op.
    assert {"kind", "holder_kind", "direction", "status", "source"} <= _enum_columns().keys()


def test_enum_fields_are_exposed_as_enum(schema: dict[str, Any]) -> None:
    columns = _enum_columns()
    problems = []
    for name, component in schema["components"]["schemas"].items():
        if name in NOT_DB_BACKED:
            continue
        for field, prop in component.get("properties", {}).items():
            if field not in columns:
                continue
            for leaf in _resolve(schema, prop):
                if leaf.get("type") == "null":
                    continue
                if "enum" not in leaf:
                    problems.append(f"{name}.{field} é string livre")
                elif set(leaf["enum"]) not in columns[field]:
                    problems.append(f"{name}.{field} = {leaf['enum']} diverge do CHECK do banco")
    assert not problems, problems


def test_health_status_is_enum(schema: dict[str, Any]) -> None:
    status = schema["components"]["schemas"]["HealthResponse"]["properties"]["status"]

    assert set(status["enum"]) == {"ok", "degraded"}
