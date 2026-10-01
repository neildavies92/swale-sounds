from pathlib import Path

import pytest
import yaml


@pytest.fixture
def config_data() -> dict:
    path = Path(__file__).parents[1] / "config/swale-sounds.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture
def spec_data():
    path = Path(__file__).parents[1] / "examples/rainy-paris.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture
def spec_file(tmp_path, spec_data):
    path = tmp_path / "input.yaml"
    path.write_text(yaml.safe_dump(spec_data), encoding="utf-8")
    return path


@pytest.fixture
def session_environment(tmp_path, config_data):
    from alembic import command
    from alembic.config import Config

    from swale_sounds.config import load_config
    from swale_sounds.database import create_database_engine

    config_data["paths"]["data"] = str(tmp_path / "data")
    config_data["database"]["url"] = f"sqlite:///{tmp_path}/state.db"
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    settings = load_config(path)
    migration = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    migration.set_main_option("sqlalchemy.url", settings.database.url)
    command.upgrade(migration, "head")
    engine = create_database_engine(settings.database)
    try:
        yield settings, engine, path
    finally:
        engine.dispose()
