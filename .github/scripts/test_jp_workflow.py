"""The workflow selects one release and derives JP endpoints without copying credentials."""
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import story_site
import music_data


def test_configure_jp_from_public_snapshot(monkeypatch):
    monkeypatch.setenv('MASTERDATA_REGION', 'jp')
    monkeypatch.setenv('NNNOTES_CATALOG_REGION', 'jp')
    entry = {'client_version': '1.0.4', 'assets': {'api_root': 'https://api.example.test',
             'bundle_root': 'https://cdn.example.test/asset/1.0.0.300/Android/' + 'a' * 32}}
    monkeypatch.setattr(story_site, 'master_index', lambda: ('unused', {'entry': entry}))
    for key in ('API', 'CDN', 'CLIENT_VERSION', 'PROVIDER'):
        monkeypatch.setenv('NNNOTES_SERVERS_JP_' + key, '')
    story_site.configure_region()
    assert os.environ['NNNOTES_SERVERS_JP_CDN'] == 'https://cdn.example.test'
    assert os.environ['NNNOTES_SERVERS_JP_API'] == 'https://api.example.test'
    assert os.environ['NNNOTES_SERVERS_JP_CLIENT_VERSION'] == '1.0.4'


def test_reject_mixed_master_and_catalog(monkeypatch):
    monkeypatch.setenv('MASTERDATA_REGION', 'jp')
    monkeypatch.setenv('NNNOTES_CATALOG_REGION', 'tw')
    with pytest.raises(SystemExit, match='differ'):
        story_site.configure_region()


def test_asset_hash_changes_build_inputs(monkeypatch):
    monkeypatch.setenv('MASTERDATA_REGION', 'jp')
    monkeypatch.setattr(music_data, 'deck_commit', lambda root: 'a' * 40)
    monkeypatch.setattr(music_data, 'nnnotes_commit', lambda root: 'b' * 40)
    a = music_data.inputs({'version': 'v', 'resource_hash': 'a' * 32})
    b = music_data.inputs({'version': 'v', 'resource_hash': 'b' * 32})
    assert a != b and a['resourceHash'] == 'a' * 32


def test_jp_catalog_provenance_matches_snapshot():
    from test_music_data import sample
    doc = sample()
    doc['provenance']['catalog']['resourceVersion'] = '1.0.0.300'
    doc['provenance']['catalog']['resourceHash'] = 'a' * 32
    context = music_data.Context(snapshot={'region': 'jp', 'entry': {
        'version': doc['provenance']['master']['version'], 'resource_version': '1.0.0.300', 'resource_hash': 'b' * 32}})
    gate = music_data.Gate()
    music_data.gate_provenance(doc, context, gate)
    assert any('resourceHash differs' in s for s in gate.failures)


def test_regions_follow_the_dispatch_payload():
    assert story_site.select_regions('hk-tw-mo jp', 'repository_dispatch', ['jp'], '') == ['jp']
    assert story_site.select_regions('hk-tw-mo jp', 'repository_dispatch', ['hk-tw-mo', 'en', 'kr'], '') == ['hk-tw-mo']
    # an older dispatch without regions: every enabled region
    assert story_site.select_regions('hk-tw-mo,jp', 'repository_dispatch', None, '') == ['hk-tw-mo', 'jp']


def test_regions_of_schedule_and_manual_runs():
    assert story_site.select_regions('hk-tw-mo jp', 'schedule', None, '') == ['hk-tw-mo', 'jp']
    assert story_site.select_regions('hk-tw-mo jp', 'workflow_dispatch', None, 'all') == ['hk-tw-mo', 'jp']
    assert story_site.select_regions('hk-tw-mo jp', 'workflow_dispatch', None, 'jp') == ['jp']
    # a region STORY_REGIONS leaves out is not built
    assert story_site.select_regions('hk-tw-mo', 'workflow_dispatch', None, 'jp') == []


def test_regions_reject_a_region_without_a_story_site():
    with pytest.raises(SystemExit, match='no story site'):
        story_site.select_regions('hk-tw-mo kr', 'schedule', None, '')


def test_regions_command_reads_the_event(tmp_path, monkeypatch):
    import json
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'client_payload': {'regions': ['jp']}}), encoding='utf-8')
    out = tmp_path / 'out'
    monkeypatch.setenv('GITHUB_EVENT_PATH', str(event))
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'repository_dispatch')
    monkeypatch.setenv('GITHUB_OUTPUT', str(out))
    monkeypatch.setenv('STORY_REGIONS', 'hk-tw-mo jp')
    story_site.cmd_regions()
    assert out.read_text(encoding='utf-8').splitlines() == ['regions=["jp"]', 'count=1']
