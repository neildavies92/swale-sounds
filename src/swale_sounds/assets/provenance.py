"""Provider and licence evidence supplied by the operator."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class AssetError(ValueError):
    """An expected import or manifest failure with operator context."""


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    provider: str = Field(default="manual", min_length=1, pattern=r"\S")
    provider_model: str | None = None
    provider_plan: str | None = None
    generation_prompt: str | None = None
    generation_parameters: dict[str, JsonValue] | None = None
    licence_notes: str | None = None
    licence_url: str | None = None
    licence_version: str | None = None

    @field_validator("generation_parameters")
    @classmethod
    def finite_json(
        cls, value: dict[str, JsonValue] | None
    ) -> dict[str, JsonValue] | None:
        json.dumps(value, allow_nan=False)
        return value


def load_provenance(
    *,
    provider: str = "manual",
    provider_model: str | None = None,
    provider_plan: str | None = None,
    licence_notes: str | None = None,
    licence_url: str | None = None,
    licence_version: str | None = None,
    prompt_file: Path | None = None,
    parameters_file: Path | None = None,
) -> Provenance:
    try:
        prompt = (
            prompt_file.read_text(encoding="utf-8") if prompt_file else None
        )
        parameters: object = None
        if parameters_file:
            parameters = json.loads(
                parameters_file.read_text(encoding="utf-8")
            )
            if not isinstance(parameters, dict):
                raise ValueError("generation parameters must be a JSON object")
            # Reject NaN/Infinity accepted by Python's permissive JSON decoder.
            json.dumps(parameters, allow_nan=False)
        return Provenance.model_validate(
            {
                "provider": provider,
                "provider_model": provider_model,
                "provider_plan": provider_plan,
                "licence_notes": licence_notes,
                "licence_url": licence_url,
                "licence_version": licence_version,
                "generation_prompt": prompt,
                "generation_parameters": parameters,
            }
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise AssetError(f"Invalid provenance input: {exc}") from exc
