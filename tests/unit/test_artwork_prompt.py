from swale_sounds.generation.artwork import build_prompt
from swale_sounds.sessions.schema import SessionSpec


def test_prompt_uses_normalized_metadata_in_stable_order(spec_data):
    spec_data["session"]["title"] = " Café à Paris 東京 "
    spec_data["music"]["mood"] = ["Quiet Focus", "Peaceful"]
    spec = SessionSpec.model_validate(spec_data)
    prompt = build_prompt(spec)
    assert prompt == build_prompt(
        SessionSpec.model_validate(spec.model_dump())
    )
    assert "Café à Paris 東京" in prompt
    assert "Atmosphere: quiet focus, peaceful." in prompt
    assert "Environment: cafe." in prompt and "Location: paris." in prompt
    assert "Weather: rain." in prompt and "Time: night." in prompt
    assert (
        "Season: autumn." in prompt and "Visual style: illustrated." in prompt
    )
    assert (
        "Still-image visual motifs: rain, steam, window reflections." in prompt
    )
    assert prompt.index("Atmosphere:") < prompt.index("Environment:")
    assert "text, titles, captions, logos, watermarks" in prompt
    assert "UI or interface elements, borders, or readable signage" in prompt
    assert "Swale Sounds" not in prompt
    assert prompt.endswith("\n") and not prompt.endswith("\n\n")


def test_prompt_omits_empty_sections_and_preserves_list_order(spec_data):
    spec_data.update(music={}, context={}, visual={})
    spec = SessionSpec.model_validate(spec_data)
    prompt = build_prompt(spec)
    for section in (
        "Atmosphere:",
        "Location:",
        "Visual style:",
        "Still-image visual motifs:",
    ):
        assert section not in prompt
    spec.context.purpose = ["reading", "focus"]
    assert "Purpose: reading, focus." in build_prompt(spec)
    spec.context.purpose.reverse()
    assert "Purpose: focus, reading." in build_prompt(spec)
