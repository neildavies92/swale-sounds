from pathlib import Path

import pytest
import yaml


@pytest.fixture
def config_data() -> dict:
    path = Path(__file__).parents[1] / "config/swale-sounds.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))
