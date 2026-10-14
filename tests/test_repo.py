"""Repository metadata: the readmes, the documentation site and the commit message check."""

import re
import tomllib
from pathlib import Path

import pytest

from nnnotes import schemas

ROOT = Path(__file__).resolve().parents[1]
REPO_BLOB = "https://github.com/MetaSekaiLab/nnnotes/blob/main/"
DOCS_SITE = "https://metasekailab.github.io/nnnotes/"


def links(text: str) -> list[str]:
    return re.findall(r"\]\(([^)\s]+)\)", text)


@pytest.mark.parametrize("name", ["README.md", "README.zh-CN.md"])
def test_readmes_have_absolute_links(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    found = links(text)
    assert found
    # a package index has no repository to resolve relative links in
    assert [u for u in found if not re.match(r"https?://", u)] == []
    for u in found:
        if u.startswith(REPO_BLOB):
            assert (ROOT / u[len(REPO_BLOB) :]).is_file(), u
        if u.startswith(DOCS_SITE):
            page = u[len(DOCS_SITE) :].split("#")[0].strip("/")
            assert (ROOT / "docs" / f"{page or 'index'}.md").is_file(), u


def test_the_package_page_is_the_english_readme():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert meta["readme"] == "README.md"
    assert meta["urls"]["Documentation"] == DOCS_SITE


def test_the_translation_follows_the_readme():
    def shape(name):
        text = (ROOT / name).read_text(encoding="utf-8")
        return [len(h) for h in re.findall(r"^(#+) ", text, re.M)], [u for u in links(text) if DOCS_SITE in u]

    assert shape("README.zh-CN.md") == shape("README.md")


def test_every_docs_page_is_in_the_site_navigation():
    nav = re.findall(r"^\s+- [^:]+: ([\w-]+\.md)$", (ROOT / "mkdocs.yml").read_text(encoding="utf-8"), re.M)
    assert sorted(nav) == sorted(p.name for p in (ROOT / "docs").glob("*.md"))
    assert len(set(nav)) == len(nav)


def test_the_schema_page_lists_every_schema():
    text = (ROOT / "docs" / "schemas.md").read_text(encoding="utf-8")
    assert re.findall(r"^\| `([\w-]+)` \|", text, re.M) == schemas.names()


def workflow_triggers(text: str) -> list[str]:
    """The event names under the top-level `on:` of a workflow file."""
    lines = text.splitlines()
    start = lines.index("on:")
    events = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break
        m = re.fullmatch(r"  ([a-z_]+):.*", line)
        if m:
            events.append(m.group(1))
    return events


def test_commit_messages_are_checked_on_pull_requests_and_dispatched_branches():
    text = (ROOT / ".github" / "workflows" / "commitlint.yml").read_text(encoding="utf-8")
    assert workflow_triggers(text) == ["pull_request", "workflow_dispatch"]
    assert "event_name == 'push'" not in text and "github.event.before" not in text
    assert "github.event.pull_request.title" in text and "github.event.pull_request.base.sha" in text


def test_the_deck_pin_pull_request_gets_its_checks():
    workflows = ROOT / ".github" / "workflows"
    deck = (workflows / "deck.yml").read_text(encoding="utf-8")
    for name in ("ci.yml", "commitlint.yml"):
        assert "workflow_dispatch" in workflow_triggers((workflows / name).read_text(encoding="utf-8"))
        assert f'gh workflow run {name} --repo "$GITHUB_REPOSITORY" --ref deck/update' in deck


@pytest.mark.parametrize("name", ["README.md", "README.zh-CN.md"])
def test_determinism_is_stated_with_its_condition(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "Pillow" in text and "1e999" in text


def test_the_changelog_starts_with_the_generated_header():
    header = tomllib.loads((ROOT / "cliff.toml").read_text(encoding="utf-8"))["changelog"]["header"]
    assert "Semantic Versioning" in header
    assert (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").startswith(header.rstrip("\n") + "\n\n## ")
