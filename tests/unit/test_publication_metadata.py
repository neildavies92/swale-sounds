import pytest

from swale_sounds.models import Asset
from swale_sounds.publishing.metadata import (
    description_for,
    music_provenance,
    readable,
    tags_for,
    title_for,
)
from swale_sounds.publishing.models import PublicationError, tags_length
from swale_sounds.sessions.schema import SessionSpec


def music(notes=None, **kwargs):
    return Asset(
        public_id="asset-music",
        original_filename="song.wav",
        provider="youtube_audio_library",
        licence_notes=notes,
        **kwargs,
    )


def test_readable_and_deterministic_title(spec_data):
    spec = SessionSpec.model_validate(spec_data)
    spec.session.title = "rainy_paris café"
    spec.music.genre = ["jazz"]
    spec.music.mood = ["cozy", "peaceful"]
    spec.context.purpose = ["reading", "focus", "sleep"]
    assert readable("quiet_focus") == "quiet focus"
    assert (
        title_for(spec) == "Rainy Paris Café | Cozy Jazz for Reading & Focus"
    )
    assert title_for(spec) == title_for(spec)


def test_long_unicode_title(spec_data):
    spec = SessionSpec.model_validate(spec_data)
    spec.session.title = "東京 café " * 50
    result = title_for(spec)
    assert len(result) <= 100 and result.endswith("…")
    assert "東京" in result and "Café" in result
    assert result.encode().decode() == result


def test_title_empty_dimensions_and_forbidden_characters(spec_data):
    spec = SessionSpec.model_validate(spec_data)
    spec.session.title = "<Paris>"
    spec.music.genre = spec.music.mood = spec.context.purpose = []
    assert title_for(spec) == "Paris"


def test_tags_stable_deduplicated_and_bounded(spec_data):
    spec = SessionSpec.model_validate(spec_data)
    spec.music.genre = ["jazz", "quiet_focus"]
    spec.music.mood = ["jazz"]
    spec.context.purpose = ["quiet_focus", "reading"]
    spec.context.location = ["x" * 600, "paris"]
    tags = tags_for(spec)
    assert tags[:3] == ["jazz", "quiet focus", "reading"]
    assert len(tags) == len(set(tags))
    assert "paris" in tags and "x" * 600 not in tags
    assert tags_length(tags) <= 500
    assert tags == tags_for(spec)
    assert tags_length(["quiet focus", "jazz"]) == 18


@pytest.mark.parametrize(
    "provider", ["youtube_audio_library", "manual", "other"]
)
def test_provenance_no_attribution_and_description(spec_data, provider):
    asset = music(
        "Attribution: not required",
        licence_url="https://example.org/terms",
        licence_version="v1",
        provider_model="catalogue-v1",
    )
    asset.provider = provider
    evidence = music_provenance(asset)
    assert evidence.attribution == "not_required"
    assert evidence.licence_url == asset.licence_url
    assert evidence.licence_version == "v1"
    spec = SessionSpec.model_validate(spec_data)
    description = description_for(spec, [evidence])
    assert "Attribution: not required" in description
    assert "Licence URL: https://example.org/terms" in description
    assert "Licence version: v1" in description
    assert description == description_for(spec, [evidence])
    assert "own" not in description


@pytest.mark.parametrize(
    "notes",
    [
        "Attribution: required",
        "Attribution required",
        "Credit required",
        "CC BY 4.0",
        "Attribution: required\nAttribution text:   ",
        "Attribution: required\nAttribution text:\nLicence version: v1",
        "Attribution: required; Attribution: not required",
    ],
)
def test_missing_or_ambiguous_credit_fails(notes):
    with pytest.raises(PublicationError, match="asset-music"):
        music_provenance(music(notes))


def test_required_credit_preserved_verbatim(spec_data):
    notes = "Attribution: required\nAttribution text: Café by Artist — CC BY"
    evidence = music_provenance(music(notes))
    assert evidence.attribution_text == "Café by Artist — CC BY"
    assert notes in description_for(
        SessionSpec.model_validate(spec_data), [evidence]
    )


def test_absent_provenance_never_infers_rights(spec_data):
    evidence = music_provenance(music())
    assert evidence.attribution == "unknown"
    description = description_for(
        SessionSpec.model_validate(spec_data), [evidence]
    )
    assert (
        "Attribution requirements not recorded; review needed." in description
    )
    assert "not required" not in description


@pytest.mark.parametrize(
    "notes",
    [
        "No attribution required.",
        "Attribution not required",
        "Attribution is not required",
        "Attribution: not required",
    ],
)
def test_existing_explicit_no_attribution_wording(notes):
    evidence = music_provenance(music(notes))
    assert evidence.attribution == "not_required"
    assert evidence.licence_notes == notes


@pytest.mark.parametrize(
    "notes",
    [
        "Attribution: required\nAttribution text: " + "é" * 2600,
        "Attribution: required\nAttribution text: <artist>",
    ],
)
def test_credit_never_silently_truncated_or_rewritten(spec_data, notes):
    with pytest.raises(PublicationError):
        description_for(
            SessionSpec.model_validate(spec_data),
            [music_provenance(music(notes))],
        )
