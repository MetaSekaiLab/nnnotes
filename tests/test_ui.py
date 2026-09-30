"""UI export/resume contracts with synthetic objects, images and split packages only."""
import json
from types import SimpleNamespace as NS
import zipfile

from PIL import Image
import pytest

from nnnotes import cli, ui
from nnnotes.config import Config

KEY = 'EmbUI/Example/Box'


class Catalog:
    def __init__(self, data=b'synthetic catalog', *args, **kwargs):
        self._sources = {'remote': data, 'apk': data}

    def keys(self, prefix):
        return [k for k in [KEY, KEY + 'Alias'] if k.startswith(prefix)]

    def _entry(self, key):
        return {'internal_id': 'Assets/Example/Box.prefab'}


class Exporter:
    exports = 0
    controller_exports = 0
    fail = False

    def __init__(self, *args, **kwargs):
        self.controller_obj = NS(type=NS(name='AnimatorController'), assets_file=NS(name='synthetic'), path_id=3)
        texture = NS(type=NS(name='Texture2D'), read=lambda: NS(image=Image.new('RGBA', (2, 2), (8, 16, 24, 255))))
        self.tex_objs = {'synthetic:7': texture}
        self.sprite_recs = {}
        self._objects = {('synthetic', 3): self.controller_obj}
        self._go_file = {1: 'synthetic', 2: 'synthetic'}

    def export_key(self, key):
        type(self).exports += 1
        if self.fail:
            raise RuntimeError('synthetic unsupported component')
        return {'key': key, 'nodes': [{'nodeId': ui.identity('synthetic:1'), 'path': 'Box',
                                      'rect': {'m_SizeDelta': {'x': -30, 'y': 90}}, 'components': []}]}

    def load(self, key):
        transforms = {10: {'m_Father': {'m_PathID': 0}, 'm_GameObject': {'m_PathID': 1}},
                      20: {'m_Father': {'m_PathID': 0}, 'm_GameObject': {'m_PathID': 2}}}
        return NS(objects=[self.controller_obj]), NS(tf=transforms, go={1: {'m_Name': 'Box'}, 2: {'m_Name': 'Extra'}})

    def hierarchy(self, env, graph, pid):
        return [{'nodeId': ui.identity('synthetic:2'), 'path': 'Extra', 'components': []}]

    def controller(self, obj):
        assert obj is self.controller_obj, 'the new exporter must use its own loaded object'
        type(self).controller_exports += 1
        return {'name': 'Example', 'clips': []}


@pytest.fixture
def exporter(monkeypatch):
    Exporter.exports = Exporter.controller_exports = 0
    Exporter.fail = False
    monkeypatch.setattr(ui, 'UIExporter', Exporter)
    return Exporter


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_export_preserves_signed_rects_and_stable_dependency_identity(tmp_path, exporter):
    index = ui.export_library(Catalog(), tmp_path, keys=[KEY])
    entry = index['assets'][0]
    assert entry['kind'] == 'prefab'
    pack = read(tmp_path / entry['file'])
    assert pack['document']['nodes'][0]['rect']['m_SizeDelta']['x'] == -30
    assert len(index['embedded']) == len(index['controllers']) == 1
    assert entry['dependencies']['controllers'] == [index['controllers'][0]['id']]
    assert all((tmp_path / name).is_file() for name in entry['files'])
    assert index['runtime_verified'] is False


@pytest.mark.parametrize('damage', ['pack', 'texture', 'controller', 'embedded'])
def test_resume_repairs_corrupt_or_missing_dependencies(tmp_path, exporter, damage):
    first = ui.export_library(Catalog(), tmp_path, keys=[KEY])
    before = exporter.exports
    ui.export_library(Catalog(), tmp_path, keys=[KEY])
    assert exporter.exports == before
    entry = first['assets'][0]
    target = entry['file'] if damage == 'pack' else ('textures/' + ui.identity('synthetic:7') + '.png' if damage == 'texture'
             else first['controllers' if damage == 'controller' else 'embedded'][0]['file'])
    (tmp_path / target).write_bytes(b'corrupt')
    result = ui.export_library(Catalog(), tmp_path, keys=[KEY])
    assert exporter.exports == before + 1
    assert (tmp_path / target).read_bytes() != b'corrupt'
    assert ui._valid_record(tmp_path, result['assets'][0])


def test_enabling_dependency_export_after_a_minimal_export(tmp_path, exporter):
    first = ui.export_library(Catalog(), tmp_path, keys=[KEY], dependencies=False)
    assert not first['controllers'] and not first['embedded']
    complete = ui.export_library(Catalog(), tmp_path, keys=[KEY])
    assert len(complete['controllers']) == len(complete['embedded']) == 1


def test_force_exports_a_shared_controller_once_and_keeps_unrelated_entries(tmp_path, exporter):
    ui.export_library(Catalog(), tmp_path, keys=[KEY])
    exporter.controller_exports = 0
    result = ui.export_library(Catalog(), tmp_path, force=True)
    assert len(result['assets']) == 2
    assert exporter.controller_exports == 1


def test_a_different_input_cannot_mix_with_an_existing_library(tmp_path, exporter):
    ui.export_library(Catalog(), tmp_path, keys=[KEY])
    original = (tmp_path / 'index.json').read_bytes()
    with pytest.raises(ValueError, match='separate output directory'):
        ui.export_library(Catalog(b'another release'), tmp_path, keys=[KEY])
    assert (tmp_path / 'index.json').read_bytes() == original


def test_resume_rejects_paths_outside_the_output(tmp_path, exporter):
    result = ui.export_library(Catalog(), tmp_path, keys=[KEY])
    result['assets'][0]['files']['../outside.png'] = 'invalid'
    (tmp_path / 'index.json').write_text(json.dumps(result), encoding='utf-8')
    with pytest.raises(ValueError, match='outside'):
        ui.export_library(Catalog(), tmp_path, keys=[KEY])


def test_failure_is_an_explicit_index_entry(tmp_path, exporter):
    exporter.fail = True
    result = ui.export_library(Catalog(), tmp_path, keys=[KEY])
    assert result['counts'] == {'failed': 1}
    assert result['assets'][0]['error_type'] == 'RuntimeError'


def test_cli_selects_the_jp_split_apk_and_keeps_public_provenance(tmp_path, exporter, monkeypatch, capsys):
    from nnnotes import catalog
    apk = tmp_path / 'jp'
    apk.mkdir()
    with zipfile.ZipFile(apk / 'base.apk', 'w') as z:
        z.writestr('assets/boot-data', b'synthetic boot')
    with zipfile.ZipFile(apk / 'split_Unity.apk', 'w') as z:
        z.writestr(catalog.APK_CATALOG, b'synthetic catalog')
    monkeypatch.setattr(catalog, 'Catalog', Catalog)
    monkeypatch.setattr(cli, 'player_data', lambda cfg: None)
    cfg = Config({'catalog': {'region': 'jp'}, 'servers': {'jp': {'apk': str(apk)}},
                  'paths': {'apk': 'wrong-global.apk', 'cache': str(tmp_path / 'cache')}}, environ={})
    args = cli.build_parser().parse_args(['ui', '--key', KEY, '-o', str(tmp_path / 'out')])
    ui.command(args, cfg)
    index = read(tmp_path / 'out/index.json')
    assert index['provenance']['region'] == 'jp'
    assert len(index['provenance']['apk_members_sha256']) == 64
    assert 'exported' in capsys.readouterr().out


def test_limit_is_validated_before_any_apk_read(tmp_path, capsys):
    args = cli.build_parser().parse_args(['ui', '--limit', '0', '-o', str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        ui.command(args, Config(environ={}))
    assert exc.value.code == 2
    assert '--limit must be positive' in capsys.readouterr().err


def test_all_used_numeric_font_faces_keep_independent_metrics_and_resource_hashes(tmp_path):
    atlas = NS(type=NS(name='Texture2D'), assets_file=NS(name='synthetic-font'), path_id=9,
               read=lambda: NS(image=Image.new('RGBA', (2, 2), (128, 128, 128, 128))))
    objects = {}
    names = ['VibeMO Synthetic Regular', 'VibeMO Synthetic Medium']
    for i, name in enumerate(names):
        tree = {'m_Name': name, 'm_AtlasTextures': [{}], 'm_FaceInfo': {'m_PointSize': 32},
                'm_CharacterTable': [{'m_Unicode': 65, 'm_GlyphIndex': 1}], 'm_GlyphTable': [{'m_Index': 1}]}
        objects[name] = NS(type=NS(name='MonoBehaviour'), path_id=i, assets_file=NS(name='synthetic-font'), read_typetree=lambda tree=tree: tree)
    ex = NS(_objects=objects, tex_objs={}, sprite_recs={}, script_class=lambda o: 'TMP_FontAsset', deref=lambda o, ptr: atlas)
    doc = {'nodes': [{'components': [{'type': 'MonoBehaviour', 'class': 'TextMeshProUGUI', 'm_fontAsset': {'name': name}}]} for name in names]}
    table = ui.resources(ex, doc, tmp_path)
    assert set(table['fontMetricsByAsset']) == set(names)
    assert len(set(table['fontMetricsByAsset'].values())) == 2
    for file in table['fontMetricsByAsset'].values():
        data = read(tmp_path / file)
        assert data['textureBase'] == 'metrics'
        assert (tmp_path / file).parent.joinpath(data['texture']).is_file()
