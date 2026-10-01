"""Deterministic, atomically replaced manifests derived from SQLite."""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine, select

from swale_sounds.assets.files import safe_child, safe_workspace
from swale_sounds.assets.provenance import AssetError
from swale_sounds.database import create_session_factory
from swale_sounds.models import Asset, Session
from swale_sounds.sessions.service import SessionNotFoundError


def manifest_bytes(public_id: str, assets: list[Asset]) -> bytes:
    records: list[dict[str, object]] = []
    for asset in sorted(
        assets, key=lambda item: (item.kind.value, item.path, item.public_id)
    ):
        record: dict[str, object] = {"asset_id": asset.public_id}
        for column in Asset.__table__.columns:
            if column.name in {"id", "public_id", "session_id"}:
                continue
            value: object = getattr(asset, column.name)
            record[column.name] = (
                value.isoformat() if isinstance(value, datetime) else value
            )
        records.append(record)
    return (
        json.dumps(
            {
                "manifest_version": 1,
                "session_id": public_id,
                "assets": records,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def write_manifest(path: Path, contents: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".assets-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def regenerate_manifest(
    engine: Engine, data_root: Path, public_id: str
) -> Path:
    try:
        # Serialize snapshot + replace with imports and other regenerations.
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            with create_session_factory(engine)(bind=connection) as db:
                session = db.scalar(
                    select(Session).where(Session.public_id == public_id)
                )
                if session is None:
                    raise SessionNotFoundError(
                        f"Session not found: {public_id}"
                    )
                workspace = safe_workspace(data_root, session.workspace_path)
                path = safe_child(workspace, "manifests/assets.json")
                assets = list(
                    db.scalars(
                        select(Asset).where(Asset.session_id == session.id)
                    )
                )
                write_manifest(path, manifest_bytes(public_id, assets))
                return path
    except OSError as exc:
        raise AssetError(
            f"Cannot write manifest for {public_id}: {exc}"
        ) from exc
