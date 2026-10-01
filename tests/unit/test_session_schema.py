from copy import deepcopy

import pytest
import yaml
from pydantic import ValidationError

from swale_sounds.sessions.schema import (
    SessionSpec,
    SessionSpecError,
    canonical_bytes,
    load_spec,
    normalize_taxonomy,
)


def test_example_is_valid(spec_file):
    spec = load_spec(spec_file)
    assert spec.session.title == "rainy paris cafe"
    assert spec.output.duration_minutes == 90


@pytest.mark.parametrize("title", ["", "   ", 42, None])
def test_title_requires_meaningful_string(spec_data, title):
    spec_data["session"]["title"] = title
    with pytest.raises(ValidationError, match="title"):
        SessionSpec.model_validate(spec_data)


def test_missing_title_is_rejected(spec_data):
    del spec_data["session"]["title"]
    with pytest.raises(ValidationError, match="title"):
        SessionSpec.model_validate(spec_data)


@pytest.mark.parametrize("duration", [0, -1, 721, 1.5, "90", True])
def test_duration_requires_integer_within_v1_bounds(spec_data, duration):
    spec_data["output"]["duration_minutes"] = duration
    with pytest.raises(ValidationError, match="duration_minutes"):
        SessionSpec.model_validate(spec_data)


@pytest.mark.parametrize(
    "bpm",
    [
        {"min": -1, "max": 70},
        {"min": 0, "max": 70},
        {"min": 70, "max": 301},
        {"min": 302, "max": 303},
        {"min": 90, "max": 70},
        {"min": True, "max": 70},
        {"min": 65.5, "max": 70},
    ],
)
def test_invalid_bpm_is_rejected(spec_data, bpm):
    spec_data["music"]["bpm"] = bpm
    with pytest.raises(ValidationError, match="bpm"):
        SessionSpec.model_validate(spec_data)


@pytest.mark.parametrize("version", [0, 2, -1, "1", True])
def test_unsupported_or_noninteger_version_is_rejected(spec_data, version):
    spec_data["spec_version"] = version
    with pytest.raises(ValidationError, match="spec_version"):
        SessionSpec.model_validate(spec_data)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("music", "genre"),
        ("music", "mood"),
        ("context", "location"),
        ("context", "environment"),
    ],
)
def test_taxonomy_accepts_new_categories(spec_data, section, field):
    spec_data[section][field] = ["Previously Unknown Café"]
    spec = SessionSpec.model_validate(spec_data)
    assert spec.model_dump()[section][field] == ["previously_unknown_café"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Jazz Café", "jazz_café"),
        ("Deep Focus", "deep_focus"),
        ("  cozy  ", "cozy"),
        ("Night", "night"),
        ("upright -__ bass", "upright_bass"),
        ("lo-fi", "lo_fi"),
    ],
)
def test_normalization_is_deterministic_and_idempotent(value, expected):
    assert normalize_taxonomy(value) == expected
    assert normalize_taxonomy(expected) == expected


@pytest.mark.parametrize("value", ["", " ", "---___ "])
def test_empty_normalized_taxonomy_is_rejected(spec_data, value):
    spec_data["music"]["genre"] = [value]
    with pytest.raises(
        ValidationError, match="taxonomy value must not be empty"
    ):
        SessionSpec.model_validate(spec_data)


def test_defaults_are_independent_and_unknown_fields_rejected(spec_data):
    spec_data["music"] = {}
    first = SessionSpec.model_validate(spec_data)
    second = SessionSpec.model_validate(spec_data)
    first.music.genre.append("jazz")
    assert second.music.genre == []
    spec_data["status"] = "created"
    with pytest.raises(ValidationError, match="Extra inputs"):
        SessionSpec.model_validate(spec_data)


@pytest.mark.parametrize(
    "contents",
    [
        "",
        "42",
        "- item",
        "music: [",
        "{}",
        "!!python/object:builtins.object {}",
    ],
)
def test_invalid_yaml_and_unsafe_tags_fail_clearly(tmp_path, contents):
    path = tmp_path / "bad.yaml"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(SessionSpecError, match=r"bad\.yaml"):
        load_spec(path)


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(SessionSpecError, match="Cannot read"):
        load_spec(tmp_path / "missing.yaml")


def test_invalid_utf8_is_reported(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_bytes(b"\xff")
    with pytest.raises(SessionSpecError, match="Cannot read"):
        load_spec(path)


def test_unreadable_path_is_reported(tmp_path):
    with pytest.raises(SessionSpecError, match="Cannot read"):
        load_spec(tmp_path)


def test_parser_reports_unsupported_version_with_field(spec_file, spec_data):
    spec_data["spec_version"] = 2
    spec_file.write_text(yaml.safe_dump(spec_data), encoding="utf-8")
    with pytest.raises(SessionSpecError, match=r"spec_version:.*unsupported"):
        load_spec(spec_file)


def test_equivalent_yaml_produces_identical_canonical_utf8(
    tmp_path, spec_data
):
    first = tmp_path / "a.yaml"
    second = tmp_path / "b.yaml"
    spec_data["session"]["title"] = "Paris café"
    spec_data["music"]["genre"] = ["Jazz Café"]
    changed = deepcopy(spec_data)
    changed["session"]["title"] = "  Paris café  "
    changed["music"]["genre"] = [" jazz--café "]
    first.write_text(
        yaml.safe_dump(spec_data, sort_keys=True), encoding="utf-8"
    )
    second.write_text(
        yaml.safe_dump(changed, sort_keys=False, indent=4), encoding="utf-8"
    )
    canonical = canonical_bytes(load_spec(first))
    assert canonical == canonical_bytes(load_spec(second))
    assert "café" in canonical.decode("utf-8")
    assert b"\r" not in canonical
    second.write_bytes(canonical)
    assert canonical_bytes(load_spec(second)) == canonical
