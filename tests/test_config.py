from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_project.config import load_settings

REPO_CONFIG = Path(__file__).parents[1] / "calendar-agent.toml"


def test_repository_config_is_valid() -> None:
    settings = load_settings(REPO_CONFIG)
    assert settings.limits.max_clarifications == 3


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("max_steps = 12", "max_step = 12"),  # unknown key (typo)
        ("max_steps = 12", 'max_steps = "12"'),  # wrong type
        ("require_citations = false", ""),  # missing key
    ],
)
def test_bad_config_is_an_error(tmp_path, old, new) -> None:
    bad = tmp_path / "calendar-agent.toml"
    bad.write_text(REPO_CONFIG.read_text("utf-8").replace(old, new), "utf-8")
    with pytest.raises(ValidationError):
        load_settings(bad)
