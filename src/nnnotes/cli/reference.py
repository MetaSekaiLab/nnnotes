"""The command reference: every command of the command line with its arguments and their help, read from the parser
(not from argparse's help layout, so the text is the same on every Python version). docs/cli.md is `markdown()`;
`python -m nnnotes.cli.reference FILE` writes it."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HEADER = """# Command reference

Every command of `nnnotes`, its arguments and their defaults. The global options go before or after the command
name. A command prints a short summary on standard output, or with `--json` its report as JSON; notes and errors go
to standard error. Exit status 2 is a usage or settings error.
"""


def _subparsers(p: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    return next((a for a in p._actions if isinstance(a, argparse._SubParsersAction)), None)


def _help(a) -> str:
    return " ".join((a.help or "").replace("%%", "%").split())


def _choices(sub: argparse._SubParsersAction) -> dict[str, str]:
    return {c.dest: _help(c) for c in sub._choices_actions}


def argument(a: argparse.Action) -> dict:
    """An argument: its flags (or name), value name, help and its choices, default and repetition."""
    d = {"flags": list(a.option_strings) or [a.metavar or a.dest], "help": _help(a)}
    takes = a.nargs != 0
    if a.option_strings and takes:
        d["value"] = a.metavar or a.dest.upper()
    if a.choices is not None:
        d["choices"] = [str(c) for c in a.choices]
    if a.nargs in ("+", "*"):
        d["many"] = True
    if isinstance(a, argparse._AppendAction):
        d["repeatable"] = True
    if a.required and a.option_strings:
        d["required"] = True
    if takes and a.default not in (None, argparse.SUPPRESS, False) and not isinstance(a.default, list):
        d["default"] = str(a.default)
    return d


def _groups(p: argparse.ArgumentParser) -> list[dict]:
    out = []
    for g in p._mutually_exclusive_groups:
        names = [a.option_strings[-1] for a in g._group_actions if a.option_strings]
        out.append({"of": names, "required": g.required})
    return out


def commands(parser: argparse.ArgumentParser, global_dests: set[str]) -> list[dict]:
    """[{command, help, arguments, exclusive}] of every command, in the order of the help; `global_dests`: the
    arguments every command shares (left out)."""
    out = []

    def walk(p, path, text):
        sub = _subparsers(p)
        if sub is None:
            args = [
                argument(a)
                for a in p._actions
                if not isinstance(a, argparse._HelpAction) and a.dest not in global_dests
            ]
            out.append({"command": " ".join(path), "help": text, "arguments": args, "exclusive": _groups(p)})
            return
        helps = _choices(sub)
        for name, child in sub.choices.items():
            walk(child, [*path, name], helps.get(name, ""))

    walk(parser, [], "")
    return out


def _cell(s: str) -> str:
    return s.replace("|", "\\|")


def _usage(d: dict) -> str:
    flags = ", ".join(f"`{f}`" for f in d["flags"])
    if "value" in d:
        flags += f" `{d['value']}`" + (" ..." if d.get("many") else "")
    elif d.get("many"):
        flags += " ..."
    return flags


def _facts(d: dict) -> str:
    """The choices, default and repetition of an argument, those its help does not already state."""
    facts = []
    if "choices" in d:
        facts.append("one of " + ", ".join(f"`{c}`" for c in d["choices"]))
    if "default" in d and "default" not in d["help"]:
        facts.append(f"default `{d['default']}`")
    if d.get("repeatable") and "repeatable" not in d["help"]:
        facts.append("repeatable")
    if d.get("required"):
        facts.append("required")
    return f" ({'; '.join(facts)})" if facts else ""


def _table(rows: list[dict]) -> list[str]:
    lines = ["| Argument | Description |", "| --- | --- |"]
    lines += [f"| {_cell(_usage(d))} | {_cell(d['help'] + _facts(d))} |" for d in rows]
    return lines


def markdown(parser: argparse.ArgumentParser | None = None) -> str:
    from . import build_parser
    from .common import GLOBALS

    parser = parser or build_parser()
    shared = [a for a in GLOBALS._actions if not isinstance(a, argparse._HelpAction)]
    lines = [HEADER, "## Global options", "", *_table([argument(a) for a in shared]), ""]
    for c in commands(parser, {a.dest for a in shared}):
        lines += [f"## nnnotes {c['command']}", "", c["help"], ""]
        if c["arguments"]:
            lines += [*_table(c["arguments"]), ""]
        for g in c["exclusive"]:
            names = ", ".join(f"`{n}`" for n in g["of"])
            lines += [f"{'Exactly' if g['required'] else 'At most'} one of {names}.", ""]
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    text = markdown()
    if argv:
        Path(argv[0]).write_bytes(text.encode("utf-8"))
    else:
        sys.stdout.buffer.write(text.encode("utf-8"))


if __name__ == "__main__":
    main()
