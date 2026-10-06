import pytest

from swale_sounds.assets.files import calculate_sha256
from swale_sounds.database import create_session_factory
from swale_sounds.models import Session
from swale_sounds.publishing.models import (
    PublicationError,
    PublicationPlan,
    PublicationPlanV2,
    parse_plan,
    plan_bytes,
)
from swale_sounds.publishing.records import (
    list_publications,
    record_publication,
)
from swale_sounds.publishing.service import (
    create_publication_plan,
    derive_plan,
    write_package,
)


def test_v1_upgrade_keeps_historical_publication(publication_session):
    settings, engine, _, session, _, _ = publication_session
    with create_session_factory(engine)() as db:
        v1, workspace = derive_plan(db, settings, db.get(Session, session.id))
    path = write_package(workspace, v1)
    v1_bytes = path.read_bytes()
    assert isinstance(parse_plan(v1_bytes), PublicationPlan)
    original, _ = record_publication(
        engine, settings, session.public_id, "LocalTest01"
    )
    current, _ = create_publication_plan(engine, settings, session.public_id)
    assert isinstance(current, PublicationPlanV2)
    assert current.plan_version == 2
    assert (workspace / current.thumbnail.path).is_file()
    assert list_publications(engine)[0].plan_text.encode() == v1_bytes
    assert list_publications(engine)[0].plan_sha256 == original.plan_sha256
    new, _ = record_publication(
        engine, settings, session.public_id, "LocalTest02"
    )
    assert new.plan_version == 2
    assert new.plan_text.encode() == path.read_bytes()


@pytest.mark.parametrize(
    "damage", ["missing", "tampered", "symlink", "forged_hash"]
)
def test_v2_record_requires_authentic_thumbnail(publication_session, damage):
    settings, engine, _, session, _, _ = publication_session
    plan, path = create_publication_plan(engine, settings, session.public_id)
    workspace = settings.paths.data / session.workspace_path
    image = workspace / plan.thumbnail.path
    if damage == "missing":
        image.unlink()
    elif damage == "tampered":
        image.write_bytes(b"bad")
    elif damage == "symlink":
        image.unlink()
        image.symlink_to(workspace / plan.artwork.path)
    else:
        # A decodable JPEG with a self-consistent edited hash must not pass.
        data = image.read_bytes() + b"unrelated metadata"
        forged = image.with_name(image.stem + "-forged.jpg")
        forged.write_bytes(data)
        digest = calculate_sha256(forged)
        target = image.with_name(
            f"thumbnail-{plan.thumbnail.fingerprint}-{digest}.jpg"
        )
        forged.rename(target)
        reference = plan.thumbnail.model_copy(
            update={
                "sha256": digest,
                "size_bytes": len(data),
                "path": target.relative_to(workspace).as_posix(),
            }
        )
        path.write_bytes(
            plan_bytes(plan.model_copy(update={"thumbnail": reference}))
        )
    before = path.read_bytes()
    with pytest.raises(PublicationError):
        record_publication(engine, settings, session.public_id, "LocalTest01")
    assert path.read_bytes() == before
    assert not list_publications(engine)


def test_plan_write_failure_keeps_old_thumbnail_references(
    publication_session, monkeypatch
):
    settings, engine, _, session, _, _ = publication_session
    plan, path = create_publication_plan(engine, settings, session.public_id)
    old = path.read_bytes()
    workspace = settings.paths.data / session.workspace_path
    thumb = (workspace / plan.thumbnail.path).read_bytes()
    from swale_sounds.rendering.video_service import render_video

    render_video(engine, settings, session.public_id, force=True)

    def fail(*args):
        raise OSError("interrupted")

    monkeypatch.setattr("swale_sounds.publishing.service.os.replace", fail)
    with pytest.raises(PublicationError):
        create_publication_plan(engine, settings, session.public_id)
    assert path.read_bytes() == old
    assert (workspace / plan.thumbnail.path).read_bytes() == thumb
