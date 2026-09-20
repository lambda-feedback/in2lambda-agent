"""The documentation against the code it describes.

A flag or a stage added to the code is documented in the same change, or one of
these fails and names the one that is not.
"""

import argparse
import ast
from pathlib import Path

from in2lambda_agent.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
HOW_IT_WORKS = (ROOT / "docs" / "how-it-works.md").read_text(encoding="utf-8")


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


def _stage_names() -> set[str]:
    """Every name a `StageResult` is constructed with in the pipeline."""
    source = (ROOT / "in2lambda_agent" / "pipeline.py").read_text(encoding="utf-8")
    return {
        node.args[0].value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "StageResult"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }


def test_readme_names_every_option():
    missing = [one for one in sorted(_options(build_parser())) if one not in README]
    assert not missing


def test_how_it_works_names_every_stage():
    names = _stage_names()
    assert len(names) == 9
    missing = [one for one in sorted(names) if f"`{one}`" not in HOW_IT_WORKS]
    assert not missing
