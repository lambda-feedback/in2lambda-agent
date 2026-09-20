"""Settings, read from the environment with a .env loaded if there is one."""

import os
from dataclasses import dataclass
from typing import Mapping, Optional

from dotenv import find_dotenv, load_dotenv


@dataclass
class Settings:
    """One field per variable in .env.example.

    All of them are optional: nothing the agent does today needs a credential,
    and a stage that comes to need one says which variable to set.
    """

    mathpix_app_id: Optional[str] = None
    mathpix_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    """Reads the settings.

    Args:
        env: The environment to read, defaulting to the process's own. When it
            is the process's own, a .env found from the working directory
            upwards is loaded first; values already set in the environment win.

    Returns:
        The settings. A variable that is unset or empty becomes None.
    """
    if env is None:
        load_dotenv(find_dotenv(usecwd=True))
        env = os.environ

    def read(name: str) -> Optional[str]:
        return env.get(name) or None

    return Settings(
        mathpix_app_id=read("MATHPIX_APP_ID"),
        mathpix_api_key=read("MATHPIX_API_KEY"),
        anthropic_api_key=read("ANTHROPIC_API_KEY"),
        openrouter_api_key=read("OPENROUTER_API_KEY"),
    )
