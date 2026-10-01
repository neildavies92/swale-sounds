import base64
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from openai.types import ImagesResponse

from swale_sounds.config import ArtworkGenerationConfig
from swale_sounds.generation.openai_images import (
    GenerationError,
    generate_image,
)


@pytest.fixture
def fake_sdk(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    client.images.generate.return_value = ImagesResponse(
        created=1,
        data=[
            {
                "b64_json": base64.b64encode(b"image bytes").decode(),
                "revised_prompt": "revised",
            }
        ],
        size="1536x864",
        output_format="png",
        quality="medium",
        background="opaque",
    )
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr(
        "swale_sounds.generation.openai_images.OpenAI", constructor
    )
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-credential")
    return constructor, client


def test_sdk_request_response_and_disabled_retries(fake_sdk):
    constructor, client = fake_sdk
    config = ArtworkGenerationConfig(timeout_seconds=240)
    prompt = "Exact prompt\r\n  "
    result = generate_image(prompt, config)
    constructor.assert_called_once_with(
        api_key="unit-test-credential",
        base_url="https://api.openai.com/v1",
        timeout=240,
        max_retries=0,
    )
    client.images.generate.assert_called_once_with(
        model=config.model,
        prompt=prompt,
        size="1536x864",
        quality="medium",
        output_format="png",
        background="opaque",
        moderation="auto",
        n=1,
        stream=False,
    )
    assert result.contents == b"image bytes" and result.output_format == "png"
    assert result.metadata == {
        "size": "1536x864",
        "quality": "medium",
        "output_format": "png",
        "background": "opaque",
        "revised_prompt": "revised",
    }
    assert "b64_json" not in result.metadata
    assert "unit-test-credential" not in str(result.metadata)
    client.__exit__.assert_called_once()


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        [{}, {}],
        [{}],
        [{"b64_json": "!invalid!"}],
        [{"b64_json": ""}],
        [{"b64_json": "é"}],
    ],
)
def test_invalid_provider_responses(fake_sdk, data):
    _, client = fake_sdk
    client.images.generate.return_value = ImagesResponse(created=1, data=data)
    with pytest.raises(GenerationError):
        generate_image("prompt", ArtworkGenerationConfig())
    client.images.generate.assert_called_once()


def test_absent_optional_response_metadata(fake_sdk):
    _, client = fake_sdk
    client.images.generate.return_value = ImagesResponse(
        created=1, data=[{"b64_json": "YQ=="}]
    )
    result = generate_image(
        "prompt", ArtworkGenerationConfig(output_format="webp")
    )
    assert result.metadata == {} and result.output_format == "webp"


def test_malformed_image_item_fails_clearly(fake_sdk):
    _, client = fake_sdk
    client.images.generate.return_value = ImagesResponse.model_construct(
        created=1, data=[None]
    )
    with pytest.raises(GenerationError, match="missing base64"):
        generate_image("prompt", ArtworkGenerationConfig())


@pytest.mark.parametrize(
    "kind,match",
    [
        (AuthenticationError, "authentication"),
        (PermissionDeniedError, "permission"),
        (RateLimitError, "quota"),
        (APITimeoutError, "timed out"),
        (APIConnectionError, "connect"),
        (APIStatusError, "HTTP 500"),
        (APIError, "invalid response"),
    ],
)
def test_provider_errors_never_echo_secrets_or_retry(fake_sdk, kind, match):
    _, client = fake_sdk
    request = SimpleNamespace(
        headers={"Authorization": "unit-test-credential"}
    )
    if kind == APITimeoutError:
        error = kind(request=request)
    elif kind == APIConnectionError:
        error = kind(request=request, message="unit-test-credential")
    elif kind == APIError:
        error = kind("unit-test-credential", request, body={})
    else:
        response = SimpleNamespace(
            request=request, status_code=500, headers={}
        )
        error = kind(
            "unit-test-credential",
            response=response,
            body={"secret": "unit-test-credential"},
        )
    client.images.generate.side_effect = error
    with pytest.raises(GenerationError, match=match) as raised:
        generate_image("prompt", ArtworkGenerationConfig())
    assert "unit-test-credential" not in str(raised.value)
    assert raised.value.__suppress_context__
    client.images.generate.assert_called_once()


def test_missing_credentials_does_not_construct_client(fake_sdk, monkeypatch):
    constructor, _ = fake_sdk
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(GenerationError, match="OPENAI_API_KEY is required"):
        generate_image("prompt", ArtworkGenerationConfig())
    constructor.assert_not_called()


def test_programming_errors_remain_visible(fake_sdk):
    _, client = fake_sdk
    client.images.generate.side_effect = RuntimeError("programming bug")
    with pytest.raises(RuntimeError, match="programming bug"):
        generate_image("prompt", ArtworkGenerationConfig())
