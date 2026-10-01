"""Official Image API boundary with explicit paid-call and secret handling."""

import base64
import binascii
import os
from dataclasses import dataclass

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import JsonValue

from swale_sounds.config import ArtworkGenerationConfig

MAX_PROMPT_CHARACTERS = 32000


class GenerationError(ValueError):
    """An expected generation prerequisite, provider or staging failure."""


@dataclass(frozen=True)
class GeneratedImage:
    contents: bytes
    output_format: str
    metadata: dict[str, JsonValue]


def validate_prompt(prompt: str) -> str:
    if not prompt.strip():
        raise GenerationError(
            "Artwork prompt must contain non-whitespace text."
        )
    if len(prompt) > MAX_PROMPT_CHARACTERS:
        raise GenerationError(
            f"Artwork prompt exceeds {MAX_PROMPT_CHARACTERS} characters."
        )
    return prompt


def request_parameters(
    settings: ArtworkGenerationConfig,
) -> dict[str, JsonValue]:
    return {
        "size": settings.size,
        "quality": settings.quality,
        "output_format": settings.output_format,
        "background": settings.background,
        "moderation": settings.moderation,
        "n": 1,
    }


def generate_image(
    prompt: str, settings: ArtworkGenerationConfig
) -> GeneratedImage:
    validate_prompt(prompt)
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key.strip():
        raise GenerationError(
            "OPENAI_API_KEY is required for artwork generation."
        )
    try:
        with OpenAI(
            api_key=key,
            base_url="https://api.openai.com/v1",
            timeout=settings.timeout_seconds,
            max_retries=0,
        ) as client:
            response = client.images.generate(
                model=settings.model,
                prompt=prompt,
                size=settings.size,
                quality=settings.quality,
                output_format=settings.output_format,
                background=settings.background,
                moderation=settings.moderation,
                n=1,
                stream=False,
            )
    except AuthenticationError:
        raise GenerationError(
            "OpenAI authentication failed; check OPENAI_API_KEY."
        ) from None
    except PermissionDeniedError:
        raise GenerationError(
            "OpenAI permission denied; check model access "
            "and organization verification."
        ) from None
    except RateLimitError:
        raise GenerationError(
            "OpenAI rate or quota limit reached; "
            "check your account before retrying."
        ) from None
    except APITimeoutError:
        raise GenerationError(
            "OpenAI artwork request timed out. No automatic retry was made; "
            "check provider usage before retrying."
        ) from None
    except APIConnectionError:
        raise GenerationError(
            "Cannot connect to OpenAI. No automatic retry was made."
        ) from None
    except APIStatusError as exc:
        # Never echo provider bodies/headers: they can contain secrets.
        raise GenerationError(
            f"OpenAI artwork request failed (HTTP {exc.status_code}); "
            "check model, request settings and provider status."
        ) from None
    except APIError:
        raise GenerationError(
            "OpenAI returned an invalid response; no automatic retry was made."
        ) from None
    if not isinstance(response.data, list) or len(response.data) != 1:
        raise GenerationError("OpenAI must return exactly one image.")
    image = response.data[0]
    encoded: object = getattr(image, "b64_json", None)
    if not isinstance(encoded, str):
        raise GenerationError(
            "OpenAI image response is missing base64 image data."
        )
    try:
        contents = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise GenerationError(
            "OpenAI returned invalid base64 image data."
        ) from None
    if not contents:
        raise GenerationError("OpenAI returned empty image bytes.")
    # Select ordinary scalar metadata only, never the raw response or base64.
    metadata: dict[str, JsonValue] = {}
    for name, value in (
        ("size", response.size),
        ("quality", response.quality),
        ("output_format", response.output_format),
        ("background", response.background),
        ("revised_prompt", image.revised_prompt),
    ):
        if value is not None:
            metadata[name] = value
    return GeneratedImage(
        contents, response.output_format or settings.output_format, metadata
    )
