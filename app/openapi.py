"""Publicação do contrato OpenAPI consumido pelo slate-web.

`uv run python -m app.openapi` regrava `openapi.json` na raiz do repo a partir das rotas
do app; `--check` só compara e sai com 1 se o arquivo estiver desatualizado.
"""

import argparse
import json
import sys
from importlib.metadata import version
from pathlib import Path

from fastapi.openapi.utils import get_openapi

from app.main import create_app

OPENAPI_PATH = Path(__file__).resolve().parents[1] / "openapi.json"
# Fixo em vez de `settings.app_name`: o contrato publicado não pode variar com o `.env`.
TITLE = "slate-api"


def render_openapi() -> str:
    application = create_app()
    schema = get_openapi(
        title=TITLE,
        version=version("slate-api"),
        openapi_version=application.openapi_version,
        routes=application.routes,
    )
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="falha se openapi.json divergir")
    parser.add_argument("--output", type=Path, default=OPENAPI_PATH)
    args = parser.parse_args(argv)

    rendered = render_openapi()
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != rendered:
            print(f"{args.output} desatualizado: rode `uv run python -m app.openapi`")
            return 1
        return 0
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
