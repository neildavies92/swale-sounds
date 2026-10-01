from pathlib import Path

import pytest
import yaml


@pytest.fixture(autouse=True)
def prohibit_live_image_requests(monkeypatch):
    """Paid provider calls are never part of the normal test suite."""

    def unexpected(*args, **kwargs):
        pytest.fail("Live OpenAI image requests are forbidden in pytest")

    monkeypatch.setattr("openai.resources.images.Images.generate", unexpected)


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


@pytest.fixture
def asset_session(session_environment, spec_file):
    from swale_sounds.sessions.service import create_session

    settings, engine, config = session_environment
    row = create_session(engine, settings.paths.data, spec_file)
    return settings, engine, config, row


@pytest.fixture
def media_files(tmp_path):
    import shutil
    import struct
    import wave
    import zlib

    if shutil.which("ffprobe") is None:
        pytest.skip(
            "FFmpeg/ffprobe is required for real-media integration tests"
        )
    source = tmp_path / "input"
    source.mkdir()
    music = source / "track.wav"
    with wave.open(str(music), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * 800)
    artwork = source / "cover.png"

    def chunk(kind, contents):
        return (
            struct.pack(">I", len(contents))
            + kind
            + contents
            + struct.pack(">I", zlib.crc32(kind + contents))
        )

    artwork.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 3, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x00\x80\xff" * 2) * 3))
        + chunk(b"IEND", b"")
    )
    return music, artwork


@pytest.fixture
def render_session(session_environment, spec_file, spec_data, media_files):
    import shutil

    from swale_sounds.assets.provenance import Provenance
    from swale_sounds.assets.service import import_assets
    from swale_sounds.models import AssetKind
    from swale_sounds.sessions.service import create_session

    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg is required for real rendering tests")
    settings, engine, config = session_environment
    spec_data["output"]["duration_minutes"] = 1
    spec_file.write_text(yaml.safe_dump(spec_data), encoding="utf-8")
    session = create_session(engine, settings.paths.data, spec_file)
    for source, kind in zip(
        media_files, (AssetKind.MUSIC, AssetKind.ARTWORK), strict=True
    ):
        import_assets(
            engine,
            settings.paths.data,
            session.public_id,
            source,
            kind,
            Provenance(),
        )
    return settings, engine, config, session
