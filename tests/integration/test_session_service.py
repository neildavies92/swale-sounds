import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError, OperationalError

from swale_sounds.sessions.schema import SessionSpecError, load_spec
from swale_sounds.sessions.service import (
    create_session,
    get_session,
    list_sessions,
)
from swale_sounds.sessions.workspace import SessionWorkspaceError


def test_create_persists_canonical_spec_and_relative_paths(
    session_environment, spec_file
):
    settings, engine, _ = session_environment
    first = create_session(engine, settings.paths.data, spec_file)
    second = create_session(engine, settings.paths.data, spec_file)
    assert [row.public_id for row in list_sessions(engine)] == [
        "session-000001",
        "session-000002",
    ]
    row = get_session(engine, first.public_id)
    assert row.id != second.id
    assert row.title == "rainy paris cafe"
    assert row.status.value == "created"
    assert row.spec_version == 1
    assert row.spec_json == load_spec(spec_file).model_dump(mode="json")
    assert row.workspace_path == "sessions/session-000001"
    assert row.spec_path == "sessions/session-000001/session.yaml"
    assert row.created_at.tzinfo is UTC
    assert row.updated_at.tzinfo is UTC
    canonical = (settings.paths.data / row.spec_path).read_bytes()
    assert hashlib.sha256(canonical).hexdigest() == row.spec_sha256
    assert (
        load_spec(settings.paths.data / row.spec_path).model_dump(mode="json")
        == row.spec_json
    )


def test_failed_validation_leaves_no_row_or_workspace(
    session_environment, spec_file
):
    settings, engine, _ = session_environment
    spec_file.write_text("{}", encoding="utf-8")
    with pytest.raises(SessionSpecError):
        create_session(engine, settings.paths.data, spec_file)
    assert list_sessions(engine) == []
    assert not settings.paths.data.exists()


def test_existing_workspace_rolls_back_without_removing_user_data(
    session_environment, spec_file
):
    settings, engine, _ = session_environment
    path = settings.paths.data / "sessions/session-000001"
    path.mkdir(parents=True)
    (path / "keep").write_text("important")
    with pytest.raises(SessionWorkspaceError):
        create_session(engine, settings.paths.data, spec_file)
    assert list_sessions(engine) == []
    assert (path / "keep").read_text() == "important"


def test_database_insert_failure_cleans_new_workspace(
    session_environment, spec_file
):
    settings, engine, _ = session_environment
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER reject_session BEFORE INSERT ON sessions "
                "BEGIN SELECT RAISE(ABORT, 'injected insert failure'); END"
            )
        )
    with pytest.raises(IntegrityError):
        create_session(engine, settings.paths.data, spec_file)
    assert list_sessions(engine) == []
    assert not (settings.paths.data / "sessions/session-000001").exists()


def test_database_commit_failure_rolls_back_and_cleans_workspace(
    session_environment, spec_file
):
    settings, engine, _ = session_environment

    def fail_commit(connection):
        raise OperationalError(
            "COMMIT", {}, RuntimeError("injected commit failure")
        )

    event.listen(engine, "commit", fail_commit)
    try:
        with pytest.raises(OperationalError):
            create_session(engine, settings.paths.data, spec_file)
    finally:
        event.remove(engine, "commit", fail_commit)
    assert list_sessions(engine) == []
    assert not (settings.paths.data / "sessions/session-000001").exists()
    assert (
        create_session(engine, settings.paths.data, spec_file).public_id
        == "session-000001"
    )


def test_concurrent_creations_have_distinct_sequential_ids(
    session_environment, spec_file
):
    settings, engine, _ = session_environment

    def create_one(_):
        return create_session(engine, settings.paths.data, spec_file).public_id

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(create_one, range(4)))
    assert sorted(ids) == [f"session-{number:06d}" for number in range(1, 5)]
    assert len(list_sessions(engine)) == 4
