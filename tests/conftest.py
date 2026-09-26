import shutil

import pytest

from growthops.db import connect
from growthops.seed import seed
from growthops.warehouse import build


@pytest.fixture(scope="session")
def sample_path(tmp_path_factory):
    """The full synthetic scenario, seeded once per test session."""
    path = tmp_path_factory.mktemp("scenario") / "sample.db"
    seed(str(path))
    build(str(path))
    return path


@pytest.fixture
def db_path(sample_path, tmp_path):
    """A private copy of the scenario that a test may modify."""
    path = tmp_path / "copy.db"
    shutil.copy(sample_path, path)
    return path


@pytest.fixture
def connection(db_path):
    handle = connect(db_path)
    yield handle
    handle.close()
