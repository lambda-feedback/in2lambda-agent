"""Settings come from the environment, with a .env read when there is one."""

import os

from in2lambda_agent.settings import Settings, load_settings


def test_reads_every_variable_from_a_given_environment():
    settings = load_settings(
        {
            "MATHPIX_APP_ID": "app",
            "MATHPIX_API_KEY": "mathpix",
            "ANTHROPIC_API_KEY": "anthropic",
            "OPENROUTER_API_KEY": "openrouter",
        }
    )

    assert settings == Settings(
        mathpix_app_id="app",
        mathpix_api_key="mathpix",
        anthropic_api_key="anthropic",
        openrouter_api_key="openrouter",
    )


def test_an_unset_or_empty_variable_is_none():
    # .env.example ships every variable empty, so empty must mean unset.
    assert load_settings({"MATHPIX_APP_ID": ""}) == Settings()


def test_a_dotenv_in_the_working_directory_is_loaded(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("MATHPIX_APP_ID=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MATHPIX_APP_ID", raising=False)

    assert load_settings().mathpix_app_id == "from-dotenv"


def test_no_dotenv_is_fine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for variable in (
        "MATHPIX_APP_ID",
        "MATHPIX_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)

    assert load_settings() == Settings()


def test_every_variable_in_the_example_has_a_field():
    example = os.path.join(os.path.dirname(__file__), "..", ".env.example")
    with open(example) as file:
        variables = [
            line.split("=")[0].strip()
            for line in file
            if "=" in line and not line.lstrip().startswith("#")
        ]

    fields = set(vars(Settings()))
    assert variables
    assert {variable.lower() for variable in variables} <= fields
