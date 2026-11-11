"""User settings from calendar-agent.toml, the only source of their values."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

CONFIG_PATH = Path("calendar-agent.toml")


class _Section(BaseModel):
    # strict: "5" is not an int and "yes" is not a bool; typos are errors.
    model_config = ConfigDict(extra="forbid", strict=True)


class Limits(_Section):
    max_steps: int = Field(gt=0)
    max_cost_usd: float = Field(gt=0)
    max_clarifications: int = Field(ge=0)


class Storage(_Section):
    db: str = Field(min_length=1)


class ModelSettings(_Section):
    name: str = Field(min_length=1)
    temperature: float = Field(ge=0, le=2)


class AgentSettings(_Section):
    require_citations: bool


class Settings(_Section):
    limits: Limits
    storage: Storage
    model: ModelSettings
    agent: AgentSettings


def load_settings(path: Path = CONFIG_PATH) -> Settings:
    """Raise OSError, TOMLDecodeError or ValidationError on a bad file."""
    with path.open("rb") as file:
        return Settings.model_validate(tomllib.load(file))
