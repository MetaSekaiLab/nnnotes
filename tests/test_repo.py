"""Repository metadata: the package page's readme and the commit message check."""
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO_BLOB = "https://github.com/MetaSekaiLab/nnnotes/blob/main/"


def test_package_readme_is_english_with_absolute_links():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert meta["readme"] == "README.en.md"
    text = (ROOT / meta["readme"]).read_text(encoding="utf-8")
    links = re.findall(r"\]\(([^)\s]+)\)", text)
    assert links
    relative = [u for u in links if not re.match(r"https?://", u)]
    assert relative == []                                    # a package index has no repository to resolve them in
    for u in links:
        if u.startswith(REPO_BLOB):
            assert (ROOT / u[len(REPO_BLOB):]).is_file(), u
    assert (ROOT / "README.md").is_file()                    # the repository's own page stays the Chinese one


def workflow_triggers(text: str) -> list[str]:
    """The event names under the top-level `on:` of a workflow file."""
    lines = text.splitlines()
    start = lines.index("on:")
    events = []
    for line in lines[start + 1:]:
        if line and not line.startswith(" "):
            break
        m = re.fullmatch(r"  ([a-z_]+):.*", line)
        if m:
            events.append(m.group(1))
    return events


def test_commit_messages_are_checked_on_pull_requests_only():
    text = (ROOT / ".github" / "workflows" / "commitlint.yml").read_text(encoding="utf-8")
    assert workflow_triggers(text) == ["pull_request"]
    assert "event_name == 'push'" not in text and "github.event.before" not in text
    assert "github.event.pull_request.title" in text and "github.event.pull_request.base.sha" in text


@pytest.mark.parametrize("name", ["README.md", "README.en.md"])
def test_determinism_is_stated_with_its_condition(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "Pillow" in text and "1e999" in text
