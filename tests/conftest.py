import warnings
from collections.abc import Iterator

import pytest
from starlette.exceptions import StarletteDeprecationWarning

# starlette.testclient warns, at import time, that httpx should be replaced by
# "httpx2" — a package this project does not depend on (see task brief: no new
# dependencies; httpx is the dev dependency fixed by Task 1). The filter must
# be installed before the import below triggers the warning.
warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=StarletteDeprecationWarning,
)

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def client() -> Iterator[TestClient]:
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()
