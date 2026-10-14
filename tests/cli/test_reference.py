"""The command reference: docs/cli.md is the text the parser gives, and every command and argument has help."""

from pathlib import Path

from nnnotes import cli
from nnnotes.cli import reference

ROOT = Path(__file__).resolve().parents[2]


def test_the_command_reference_is_current():
    text = (ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
    assert reference.markdown() == text, "docs/cli.md: run python -m nnnotes.cli.reference docs/cli.md"


def test_every_command_and_argument_has_help():
    found = reference.commands(cli.build_parser(), set())
    assert len(found) > 30
    for c in found:
        assert c["help"], c["command"]
        for a in c["arguments"]:
            assert a["help"], (c["command"], a["flags"])


def test_every_command_takes_the_global_options_and_json():
    for c in reference.commands(cli.build_parser(), set()):
        flags = {f for a in c["arguments"] for f in a["flags"]}
        assert {"--config", "--region", "--language", "--json"} <= flags, c["command"]
