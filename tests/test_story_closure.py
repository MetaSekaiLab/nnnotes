"""adv.closure: the resource closure AdvEpisodeResourceLoader.AddEpisodeLoadingTask builds (synthetic rows)."""
import pytest

from nnnotes import adv
from nnnotes.config import ConfigError

CMD = {v: k for k, v in adv.COMMAND.items()}


def row(cmd, **kw):
    return {"Command": CMD[cmd], **kw}


def run(rows, catalog=None, videos=None, chats=None, default="adv_transition_default/adv_transition_default"):
    catalog = catalog if catalog is not None else Everything()
    calls = []

    def chat(i):
        calls.append(("chat", i))
        return (chats or {})[i]

    def transition():
        calls.append(("transition",))
        return default
    return adv.closure(rows, catalog.__contains__, videos or {}, chat, transition), calls


class Everything:
    def __contains__(self, key):
        return True


def addresses(res):
    return [(r["kind"], r["address"]) for r in res]


def test_prefix_kinds_sorted_and_deduplicated():
    res, calls = run([
        row("Stage", TargetAssetName="st_b/st_b"),
        row("Character", TargetName="a", TargetAssetName="g/m/model/m"),
        row("Stage", TargetAssetName="st_a/st_a"),
        row("Stage", TargetAssetName="st_b/st_b"),
        row("Still", TargetAssetName="anime/s1/s1"),
        row("Frame", TargetAssetName="f/f_black"),
        row("PostEffect", TargetAssetName="pe_blur"),
        row("ChatStamp", TargetAssetName="System/stamp_1"),
    ])
    assert addresses(res) == [
        ("chatstamp", "Adv/Chat/Stamp/System/stamp_1"), ("frame", "Adv/Frame/f/f_black"),
        ("live2d", "Character/Live2D/g/m/model/m"), ("posteffect", "Adv/PostEffect/pe_blur"),
        ("stage", "Adv/Stage/st_a/st_a"), ("stage", "Adv/Stage/st_b/st_b"), ("still", "Adv/Still/anime/s1/s1")]
    assert all(r["present"] for r in res) and calls == []


def test_ignore_data_blank_names_and_costume_load_nothing():
    res, _ = run([
        row("Stage", TargetAssetName="st/st", IgnoreData=True),
        row("Still", TargetAssetName="   "),
        row("Frame", TargetAssetName=""),
        row("Costume", TargetName="a", TargetAssetIndex=1, TargetAssetName="g/m/model/m"),
        row("Talk", TargetAssetName="x"),
        row("FadeIn", TargetAssetName="t/t", IgnoreData=True),
        row("ChatWindow", TargetChatID=5, IgnoreData=True),
    ])
    assert res == []


def test_effect_needs_a_target_name_and_timeline_falls_back_to_it():
    res, _ = run([
        row("Effect", TargetAssetName="fx_a/fx_a"),
        row("Effect", TargetName="spot", TargetAssetName="fx_b/fx_b"),
        row("Timeline", TargetName="tl_a"),
    ])
    assert addresses(res) == [("effect", "Adv/Effect/fx_b/fx_b"), ("timeline", "Adv/Timeline/tl_a")]


def test_transition_default_only_for_rows_without_a_name():
    res, calls = run([row("FadeOut", TargetAssetName="adv_transition_0005/adv_transition_0005")])
    assert addresses(res) == [("transition", "Adv/Transition/adv_transition_0005/adv_transition_0005")]
    assert calls == []
    res, calls = run([row("FadeOut"), row("FadeIn", Duration=1.0)])
    assert addresses(res) == [("transition", "Adv/Transition/adv_transition_default/adv_transition_default")]
    assert calls == [("transition",), ("transition",)]


def test_talk_window_default_and_embedded_address():
    embedded = {"EmbUI/Prefab/Parts/Adv/Talk/UIDefaultTalkWindow", "EmbUI/Prefab/Parts/Adv/Talk/UICenterTalkWindow"}
    res, _ = run([row("TalkWindow"), row("TalkWindow", TargetAssetName="UICenterTalkWindow")], catalog=embedded)
    assert addresses(res) == [("talkwindow", "EmbUI/Prefab/Parts/Adv/Talk/UICenterTalkWindow"),
                              ("talkwindow", "EmbUI/Prefab/Parts/Adv/Talk/UIDefaultTalkWindow")]
    assert all(r["present"] for r in res)
    plain = {"UI/Prefab/Parts/Adv/Talk/UIDefaultTalkWindow"}
    res, _ = run([row("TalkWindow")], catalog=plain)
    assert addresses(res) == [("talkwindow", "UI/Prefab/Parts/Adv/Talk/UIDefaultTalkWindow")]


def test_videos_from_the_video_rows():
    videos = {41: {"_id": 41, "_assetName": "adv/movie_a/movie_a"}, 42: {"_id": 42, "_assetName": " "}}
    res, _ = run([row("Movie", VideoID=41), row("Clip", VideoID=41), row("Clip", VideoID=42),
                  row("Clip", VideoID=43), row("Clip", VideoID=0), row("Movie")], videos=videos)
    assert addresses(res) == [("video", "Cri/Video/adv/movie_a/movie_a")]


def test_chat_windows_icons_and_stamps():
    chats = {7: {"_id": 7, "_chatWindowAssetName": "win_7", "_chatIconAssetName": "icon_7"},
             8: {"_id": 8, "_chatWindowAssetName": "win_8", "_chatIconAssetName": ""}}
    res, calls = run([
        row("ChatWindow", TargetChatID=7),
        row("ChatTalk", TargetChatID=7),
        row("ChatTalk", TargetChatID=8),
        row("ChatStamp", TargetChatID=7, TargetAssetName="stamp_1"),
        row("ChatTyping", TargetChatID=8),
        row("ChatRead", TargetChatID=8),
        row("ChatWindow", TargetChatID=0),
    ], chats=chats)
    assert addresses(res) == [("chaticon", "Adv/Chat/Icon/icon_7"), ("chatstamp", "Adv/Chat/Stamp/stamp_1"),
                              ("chatwindow", "Adv/Chat/Prefabs/win_7")]
    assert ("chat", 0) not in calls and ("chat", 8) in calls


def test_unknown_chat_id_raises():
    with pytest.raises(KeyError):
        run([row("ChatTalk", TargetChatID=9)], chats={})


def test_presence_comes_from_the_catalog():
    res, _ = run([row("Still", TargetAssetName="a"), row("Still", TargetAssetName="b")],
                 catalog={"Adv/Still/a"})
    assert [(r["address"], r["present"]) for r in res] == [("Adv/Still/a", True), ("Adv/Still/b", False)]


def test_default_transition_needs_the_apk_catalog():
    class NoApk:
        def has(self, key):
            return False
    with pytest.raises(ConfigError, match="apk"):
        adv._default_transition(NoApk())
