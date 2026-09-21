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


def _stage_names() -> set[str]:
    """Every name the pipeline adds a stage under.

    `add_stage` is the one place a stage is recorded: it constructs the
    `StageResult` and reports it to whoever asked to be told.
    """
    source = (ROOT / "in2lambda_agent" / "pipeline.py").read_text(encoding="utf-8")
    return {
        node.args[0].value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_stage"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }


def test_readme_names_every_option():
    missing = [one for one in sorted(_options(build_parser())) if one not in README]
    assert not missing


def test_how_it_works_names_every_stage():
    names = _stage_names()
    assert len(names) == 10
    missing = [one for one in sorted(names) if f"`{one}`" not in HOW_IT_WORKS]
    assert not missing


def test_the_readme_compiles_the_ci_corpus_pdf_as_the_workflow_does():
    # The repository does not hold ci-corpus/pdf, so the reader compiles the
    # PDF before the gate reads it. The README and the workflow give the same
    # command, because another command writes other bytes, and the PDF's bytes
    # are the key the OCR cache reads under.
    for line in (
        "SOURCE_DATE_EPOCH=0 FORCE_SOURCE_DATE=1",
        "xelatex -interaction=nonstopmode -output-directory=../pdf sheet-1.tex",
    ):
        assert line in WORKFLOW
        assert line in README


def test_no_github_check_is_required_on_main():
    # The workbench merges with `gh pr merge` as soon as its own check passes,
    # and `gh pr merge` cannot wait for a GitHub check, so a required check
    # refuses every merge the workbench makes. The workflow is CI's report.
    for text in (README, WORKFLOW):
        assert "required_status_checks" not in text
        assert "branch-protection" not in text


def test_the_readme_names_the_private_baseline_by_an_absolute_path():
    # The workbench check runs in a worktree, and the worktree holds neither
    # the baseline for ExampleContents nor the specs it names.
    check = next(
        line
        for line in README.splitlines()
        if "in2lambda-agent gate " in line and "gate-baseline.json" in line
    )
    path = check.split("in2lambda-agent gate ", 1)[1].split()[0]
    assert path.startswith("/")
    assert path.endswith("/corpus-specs/gate-baseline.json")


def test_the_workflow_installs_a_pandoc_of_its_own():
    # ubuntu-24.04 packages pandoc 3.1.3, under which every ci-corpus document
    # faults on a block no selector reaches. The job installs a pinned
    # release, the one the baselines were recorded under.
    apt = WORKFLOW.split("apt-get install", 1)[1].split("\n\n", 1)[0]
    assert "pandoc" not in apt
    assert "https://github.com/jgm/pandoc/releases/download/" in WORKFLOW
