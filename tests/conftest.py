from __future__ import annotations

from datetime import datetime

import pytest

from app.db import init_db, make_engine, make_session_factory

# Wednesday 4 November 2026, 11:20 in Iași: the moment shown in the design.
NOW = datetime(2026, 11, 4, 11, 20)
CREATED = datetime(2026, 11, 4, 9, 20)  # same moment in UTC (EET = UTC+2)


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session
