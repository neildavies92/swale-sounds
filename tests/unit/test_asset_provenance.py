import pytest

from swale_sounds.assets.provenance import AssetError, load_provenance


def test_manual_provenance_defaults():
    provenance = load_provenance()
    assert provenance.provider == "manual"
    assert provenance.generation_parameters is None


def test_provenance_reads_utf8_prompt_and_json(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Paris café\n", encoding="utf-8")
    parameters = tmp_path / "parameters.json"
    parameters.write_text(
        '{"seed":42,"nested":{"tags":["jazz",null]}}', encoding="utf-8"
    )
    provenance = load_provenance(
        provider="example",
        provider_model="v1",
        provider_plan="test",
        licence_notes="Operator supplied",
        licence_url="https://example.org/terms",
        licence_version="2026",
        prompt_file=prompt,
        parameters_file=parameters,
    )
    assert provenance.generation_prompt == "Paris café\n"
    assert provenance.generation_parameters["seed"] == 42
    assert provenance.licence_version == "2026"


@pytest.mark.parametrize(
    "contents", ["[1]", "null", "bad", '{"value":NaN}', '{"value":Infinity}']
)
def test_invalid_generation_parameters_fail(tmp_path, contents):
    path = tmp_path / "parameters.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(AssetError, match="Invalid provenance"):
        load_provenance(parameters_file=path)


def test_missing_or_non_utf8_provenance_file_fails(tmp_path):
    path = tmp_path / "prompt.txt"
    with pytest.raises(AssetError):
        load_provenance(prompt_file=path)
    path.write_bytes(b"\xff")
    with pytest.raises(AssetError):
        load_provenance(prompt_file=path)


def test_blank_provider_fails():
    with pytest.raises(AssetError):
        load_provenance(provider=" ")
