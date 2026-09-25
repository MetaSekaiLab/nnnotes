"""story: resource kind dispatch, the kind files and the story command line (synthetic data)."""
import pytest

from nnnotes import adv, advmedia, cli, story


def res(kind, address, present=True):
    return {"kind": kind, "address": address, "present": present}


def test_every_closure_kind_has_an_output_but_timeline():
    produced = {k for k, _ in adv.RESOURCE_PREFIX.values()} | {"transition", "talkwindow", "video", "chatwindow",
                                                                "chaticon"}
    assert produced - set(story.KIND_OUTPUT) == {"timeline"}
    assert set(story.KIND_OUTPUT) <= produced


def test_check_kinds():
    story.check_kinds([res("live2d", "Character/Live2D/a"), res("frame", "Adv/Frame/f"), res("video", "Cri/Video/v"),
                       res("chatstamp", "Adv/Chat/Stamp/s"), res("transition", "Adv/Transition/t")])
    with pytest.raises(NotImplementedError, match="resource kind timeline: Adv/Timeline/t"):
        story.check_kinds([res("stage", "Adv/Stage/s"), res("timeline", "Adv/Timeline/t")])
    with pytest.raises(RuntimeError, match="not in catalog"):
        story.check_kinds([res("timeline", "Adv/Timeline/t"), res("still", "Adv/Still/s", present=False)])


def test_kind_files_and_index_keys():
    assert advmedia.INDEX_KEYS == ("frames", "effects", "postEffects", "stills", "talkWindows", "chat")
    assert advmedia._suffix("Adv/Frame/f/f_black", "Adv/Frame/") == "f/f_black"
    assert advmedia._suffix("EmbUI/Prefab/Parts/Adv/Talk/UICenterTalkWindow", None) == "UICenterTalkWindow"
    with pytest.raises(ValueError):
        advmedia._suffix("Adv/Still/s", "Adv/Frame/")


def test_effect_instances_first_asset_per_target_name():
    cmds = [{"cmd": "Effect", "TargetName": "spot", "TargetAssetName": "fx_a/fx_a"},
            {"cmd": "Effect", "TargetName": "spot", "Parameter2": "loop"},
            {"cmd": "Effect", "TargetName": "spot", "TargetAssetName": "fx_b/fx_b"},
            {"cmd": "Effect", "TargetName": "rain", "TargetAssetName": "fx_c/fx_c", "IgnoreData": True},
            {"cmd": "Effect", "TargetAssetName": "fx_d/fx_d"},
            {"cmd": "Still", "TargetName": "x", "TargetAssetName": "s"}]
    assert advmedia.effect_instances(cmds) == {"spot": "fx_a/fx_a"}


class _Type:
    name = "MonoBehaviour"


class _MissingScript:
    type = _Type()
    assets_file = None
    path_id = 1

    def read_typetree(self):
        return {"m_GameObject": {"m_FileID": 0, "m_PathID": 5}, "m_Enabled": 1,
                "m_Script": {"m_FileID": 0, "m_PathID": 0}, "m_Name": ""}


def test_missing_script_component_is_recorded(tmp_path):
    ex = advmedia.MediaExporter(None, tmp_path, None)
    assert ex.component(_MissingScript()) == {"type": "MonoBehaviour", "class": None, "missingScript": True,
                                              "m_Enabled": 1, "m_Name": ""}


def test_story_no_audio_flag():
    p = cli.build_parser()
    args = p.parse_args(["story", "10001", "-o", "out", "--no-audio"])
    assert args.no_audio is True and args.format == "flac"
    assert p.parse_args(["story", "10001", "-o", "out"]).no_audio is False


class _Parsed:
    def __init__(self, name):
        self.m_ParsedForm = type("P", (), {"m_Name": name})()


class _Shader:
    def __init__(self, name):
        self._name = name

    def read(self):
        return _Parsed(self._name)


class _Material:
    def read_typetree(self):
        return {"m_Name": "Font SDF Material", "m_Shader": {"m_FileID": 0, "m_PathID": 9}}


def test_open_fonts_reduce_text_materials_to_names(tmp_path, monkeypatch):
    ex = advmedia.MediaExporter(None, tmp_path, None)
    monkeypatch.setattr(ex, "deref", lambda owner, pptr: _Shader("TextMeshPro/Mobile/Distance Field"))
    m = ex.material(_Material())
    assert m == {"material": "Font SDF Material", "shader": {"shader": "TextMeshPro/Mobile/Distance Field"},
                 "note": advmedia.FONT_NOTE}
    assert "TextMeshPro/Mobile/Distance Field" in ex.shaders and not ex.textures
    assert ex.stub_assets == advmedia.TMP_ASSETS
    assert advmedia.MediaExporter(None, tmp_path, None, "game").stub_assets == ()
    with pytest.raises(ValueError):
        advmedia.MediaExporter(None, tmp_path, None, "system")
