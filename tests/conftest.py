"""Every test runs against a temporary grocery.db seeded with the synthetic fixture
catalog, and market.py's live Kroger fetch replaced by that fixture, so no test
touches the network or the real database in ~/.autoeat."""

import pytest

import grocery_fixture
from macro_optimizer import grocery_db, market


@pytest.fixture(autouse=True, scope="session")
def fixture_grocery_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("grocery") / "grocery.db"
    patch = pytest.MonkeyPatch()
    patch.setenv(grocery_db.DB_ENV_VAR, str(path))
    patch.delenv(market.SALE_PRICES_ENV_VAR, raising=False)
    conn = grocery_db.connect(path)
    grocery_fixture.seed(conn)
    conn.close()
    market.set_fetcher(grocery_fixture.fetcher)
    yield path
    market.set_fetcher(None)
    patch.undo()
