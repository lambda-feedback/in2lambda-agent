"""The documentation against the code it describes.

An option added to the command line is documented in the same change, or one of
these fails and names the option that is not.
"""

import argparse
from pathlib import Path

from in2lambda_agent.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "gate.yml").read_text(encoding="utf-8")


def _options(parser: argparse.ArgumentParser) -> set[str]:
    """Every long option of a parser and of its subcommands.

    `--help` is argparse's own and is in no documentation, and a short option is
    named beside the long one it abbreviates rather than on its own.
    """
    found = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                found |= _options(subparser)
        found |= {
            name for name in action.option_strings if name.startswith("--")
        }
    return found - {"--help"}


def test_readme_names_every_option():
    missing = [one for one in sorted(_options(build_parser())) if one not in README]
    assert not missing


def test_the_readme_runs_the_gate_as_the_workflow_does():
    # The gate is a check on a branch, so the command a reader runs and the
    # command CI runs must be the one command.
    command = "in2lambda-agent gate ci-corpus/targets --filters ci-corpus/filters"
    assert command in WORKFLOW
    assert command in README


def test_no_github_check_is_required_on_main():
    # The workbench merges with `gh pr merge` as soon as its own check passes,
    # and `gh pr merge` cannot wait for a GitHub check, so a required check
    # refuses every merge the workbench makes. The workflow is CI's report.
    for text in (README, WORKFLOW):
        assert "required_status_checks" not in text
        assert "branch-protection" not in text


def test_the_workflow_installs_a_pandoc_of_its_own():
    # ubuntu-24.04 packages pandoc 3.1.3, under which the tests fail. The job
    # installs a pinned release, the one the committed reply was written under.
    apt = WORKFLOW.split("apt-get install", 1)[1].split("\n\n", 1)[0]
    assert "pandoc" not in apt
    assert "https://github.com/jgm/pandoc/releases/download/" in WORKFLOW
