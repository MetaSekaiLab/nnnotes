"""`nnnotes config`: write the config file, set or empty one value, check the settings, show the files looked up."""

from __future__ import annotations

import sys
from pathlib import Path

from ..source.config import DEFAULT_FILE, ENV_CONFIG, ConfigError, config_files, find_file, user_file
from .common import command, group, note, print_json, report, write


def _tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def _target(args, default: Path | None) -> Path:
    """The file a config command writes: --file, --user, else `default` (None: the file the commands read)."""
    if args.file:
        return Path(args.file)
    if args.user:
        target = user_file()
        if target is None:
            raise ConfigError(
                "no per-user config directory: the environment sets neither APPDATA (Windows) nor "
                "XDG_CONFIG_HOME / HOME"
            )
        return target
    if default is not None:
        return default
    target = find_file(args.config)
    if target is None:
        raise ConfigError(
            "no config file is read (`nnnotes config path`): name one with --file or --user, or "
            "write one with `nnnotes config init`"
        )
    return target


def _value(args, name: str, text: str, stdin_used: list):
    """(section, key, value) of `name` = `text` (`-`: read from standard input, once), checked."""
    from ..source import configfile

    setting, section = configfile.resolve(name)
    if text == "-":
        if stdin_used:
            args.usage("only one value can be read from standard input")
        stdin_used.append(name)
        if setting.secret and _tty():
            import getpass

            text = getpass.getpass(f"[{section}] {setting.key} (hidden): ")
        else:
            text = sys.stdin.readline().rstrip("\r\n")
    try:
        return section, setting.key, configfile.parse_value(setting, text)
    except ValueError as e:
        raise ConfigError(f"setting {section}.{setting.key}: {e}") from None


def _read_note(target: Path) -> None:
    read = find_file()
    if read is None or read.absolute() != target.absolute():
        note(f"# the commands read {read.absolute() if read else 'no config file'} first (`nnnotes config path`)")


def cmd_init(args, cfg):
    from ..source import configfile

    named = next((f for n, f in config_files(args.config)[:2] if f is not None), None)
    target = _target(args, named or Path(DEFAULT_FILE))
    if target.exists() and not args.force:
        raise ConfigError(f"config file {target} exists (--force overwrites it; `nnnotes config set` edits one value)")
    values, regions, stdin_used = [], [], []
    for item in args.set or []:
        name, sep, text = item.partition("=")
        if not sep:
            args.usage(f"--set {name}: give it as SECTION.KEY=VALUE")
        section, key, value = _value(args, name.strip(), text, stdin_used)
        values.append((section, key, value))
        region = section.split(".", 1)[1] if section.startswith("servers.") else None
        if region and region not in regions:
            regions.append(region)
    if not args.set and not args.no_input and not args.json and _tty():
        try:
            values, regions = configfile.prompt()
        except (KeyboardInterrupt, EOFError):
            note("\nnnnotes: config init stopped, nothing written")
            sys.exit(1)
    configfile.write(target, configfile.render(values, regions))
    report(args, {"file": str(target.absolute()), "written": [f"{s}.{k}" for s, k, _ in values]}, lambda r: [r["file"]])
    note(f"# {len(values)} settings written, the others empty; `nnnotes config check` shows the status of each")
    _read_note(target)


def _edit(args, value_text: str | None) -> None:
    from ..source import configfile

    target = _target(args, None)
    text = target.read_text(encoding="utf-8") if target.exists() else configfile.template().decode("utf-8")
    if value_text is None:  # unset: an empty value
        setting, section = configfile.resolve(args.name)
        section, key, value = section, setting.key, [] if setting.kind == "list" else ""
    else:
        section, key, value = _value(args, args.name, value_text, [])
    configfile.write(target, configfile.edit(text, [(section, key, value)], str(target)))
    report(
        args,
        {"file": str(target.absolute()), "setting": f"{section}.{key}", "set": bool(value)},
        lambda r: [f"{r['file']}: {r['setting']} {'set' if r['set'] else 'emptied'}"],
    )
    _read_note(target)


def cmd_set(args, cfg):
    _edit(args, args.value)


def cmd_unset(args, cfg):
    _edit(args, None)


def _check_lines(r: dict) -> list[str]:
    lines = [f"# file: {r['file']}" if r["file"] else "# no config file is read (`nnnotes config path`)"]
    for s in r["settings"]:
        status = s["status"] + (f": {s['reason']}" if s.get("reason") else "")
        lines.append(f"{s['name']}\t{s.get('origin') or '-'}\t{status}")
    return lines


def cmd_check(args, cfg):
    from ..source import configfile

    r = configfile.check(cfg)
    report(args, r, _check_lines)
    if not args.json:
        note(f"# {r['problems']} problems")
    if r["problems"]:
        sys.exit(1)


def cmd_path(args, cfg):
    from ..source import configfile

    files, read = [], None
    for name, file in config_files(args.config):
        if file is None:
            state = "not given"
        elif not file.is_file():
            state = "not found"
        elif read is None:
            read, state = file, "read"
        else:
            state = "found, not read"
        files.append({"name": name, "path": str(file.absolute()) if file is not None else None, "state": state})
        if read is None and file is not None and name in ("--config", ENV_CONFIG):
            break  # a named file that is missing stops every command
    if args.json:
        print_json({"schema": configfile.PATHS, "files": files, "read": str(read.absolute()) if read else None})
        return
    write("".join(f"{f['name']}\t{f['path'] or '-'}\t{f['state']}\n" for f in files))
    if read is None:
        note("# no config file is read: the settings come from the environment and the flags only")


def register(sub) -> None:
    cs = group(sub, "config", "the config file: write it, set one value, check the settings, show where it is")

    def target(m, verb, default=""):
        g = m.add_mutually_exclusive_group()
        g.add_argument("--file", help=f"the config file to {verb}{default}")
        g.add_argument(
            "--user",
            action="store_true",
            help=f"{verb} the per-user config file, read from any directory (`config path` shows it)",
        )

    m = command(
        cs,
        "init",
        cmd_init,
        "write a config file from the template: the values are asked in a terminal, else taken from --set "
        "(the other settings empty)",
        reads_config=False,
    )
    target(m, "write", f" (default: the file --config or NNNOTES_CONFIG names, else ./{DEFAULT_FILE})")
    m.add_argument(
        "--set",
        action="append",
        metavar="SECTION.KEY=VALUE",
        help="a value to write (repeatable; no questions then): VALUE - reads it from standard input; a "
        "list comma-separated; a path is written absolute",
    )
    m.add_argument("--no-input", action="store_true", help="ask nothing, also in a terminal")
    m.add_argument("--force", action="store_true", help="overwrite an existing file")
    m = command(
        cs,
        "set",
        cmd_set,
        "set one value of the config file the commands read (or of --file / --user; a missing one is made "
        "from the template); comments and other lines stay",
        reads_config=False,
    )
    m.add_argument("name", metavar="SECTION.KEY", help="the setting: bundle.key, servers.tw.cdn, paths.fonts.ja, ...")
    m.add_argument(
        "value",
        help="the value (a list comma-separated); - reads it from standard input, without echo "
        "for the keys in a terminal",
    )
    target(m, "edit")
    m = command(
        cs,
        "unset",
        cmd_unset,
        "empty one value of the config file (an empty value counts as unset)",
        reads_config=False,
    )
    m.add_argument("name", metavar="SECTION.KEY", help="the setting")
    target(m, "edit")
    m = command(
        cs,
        "check",
        cmd_check,
        "every setting: where it comes from and whether it is valid, never its value; exit status 1 when one "
        "is invalid, not found or unknown (--json: nnnotes.config-check/1)",
    )
    m.set_defaults(raw_config=True)
    command(
        cs,
        "path",
        cmd_path,
        "the config files looked up, in order, and the one the commands read (--json: nnnotes.config-path/1)",
        reads_config=False,
    )
