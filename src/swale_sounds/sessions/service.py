"""Session persistence coordinated with local filesystem creation."""

import re
from pathlib import Path

from sqlalchemy import Engine, select

from swale_sounds.database import create_session_factory
from swale_sounds.models import Session, SessionStatus
from swale_sounds.sessions.schema import (
    canonical_bytes,
    load_spec,
    sha256_bytes,
)
from swale_sounds.sessions.workspace import create_workspace


class SessionNotFoundError(LookupError):
    """The requested public identifier does not exist."""


def create_session(
    engine: Engine, data_root: Path, spec_path: Path
) -> Session:
    """Create a session with serialized allocation and workspace cleanup."""
    spec = load_spec(spec_path)
    contents = canonical_bytes(spec)
    factory = create_session_factory(engine)
    with engine.connect() as connection:
        # Lock before reading IDs to serialize writers across processes.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        with factory(bind=connection) as db:
            numbers = [
                int(match[1])
                for public_id in db.scalars(select(Session.public_id))
                if (match := re.fullmatch(r"session-([0-9]+)", public_id))
            ]
            public_id = f"session-{max(numbers, default=0) + 1:06d}"
            relative = Path("sessions") / public_id
            with create_workspace(data_root, public_id, contents):
                row = Session(
                    public_id=public_id,
                    title=spec.session.title,
                    status=SessionStatus.CREATED,
                    spec_version=spec.spec_version,
                    spec_path=(relative / "session.yaml").as_posix(),
                    spec_sha256=sha256_bytes(contents),
                    spec_json=spec.model_dump(mode="json"),
                    workspace_path=relative.as_posix(),
                )
                db.add(row)
                db.flush()
                db.expunge(row)
                committed = False
                try:
                    connection.commit()
                    committed = True
                finally:
                    if not committed:
                        # A failed commit may leave driver state uncertain.
                        # Close it so an open transaction cannot be reused.
                        connection.invalidate()
            return row


def list_sessions(engine: Engine) -> list[Session]:
    with create_session_factory(engine)() as db:
        return list(db.scalars(select(Session).order_by(Session.id)))


def get_session(engine: Engine, public_id: str) -> Session:
    with create_session_factory(engine)() as db:
        row = db.scalar(select(Session).where(Session.public_id == public_id))
        if row is None:
            raise SessionNotFoundError(f"Session not found: {public_id}")
        return row
