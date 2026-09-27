"""The ADV command numbers (AdvSystem.Asset.AdvCommand): the `Command` of an episode's rows, by value.

A module of its own, with no imports, so that readers of episode data that load no bundles (the voices index) name
commands as the story export does."""

# AdvCommand enum; values 0-69, 8 and 22 unused.
COMMAND = {
    0: "In", 1: "Out", 2: "Talk", 3: "Delay", 4: "Shake", 5: "FadeOut",
    6: "FadeIn", 7: "Focus", 9: "Forward", 10: "Back", 11: "Flash",
    12: "Brightness", 13: "MoveToRight", 14: "MoveToLeft", 15: "Bgm",
    16: "SoundVolume", 17: "Expression", 18: "Pause", 19: "Resume",
    20: "Location", 21: "Motion", 23: "Character", 24: "Costume", 25: "Stage",
    26: "Movie", 27: "Clip", 28: "Subtitles", 29: "Wait", 30: "Still",
    31: "Se", 32: "Angle", 33: "Pan", 34: "Tilt", 35: "TalkWindow",
    36: "ChatWindow", 37: "ChatTalk", 38: "ChatStamp", 39: "ChatRead",
    40: "ChoiceSet", 41: "ChoiceShow", 42: "GoTo", 43: "PostEffect",
    44: "Frame", 45: "Timeline", 46: "Look", 47: "Pedestal", 48: "Track",
    49: "DoF", 50: "Role", 51: "CameraShake", 52: "Voice", 53: "Zoom",
    54: "Effect", 55: "Alpha", 56: "ForceAuto", 57: "StageEnv",
    58: "RimLight", 59: "CancelDelay", 60: "MoveToUp", 61: "MoveToDown",
    62: "MoveToForward", 63: "MoveToBack", 64: "MoveToDirection",
    65: "ChatTyping", 66: "LookTarget", 67: "PanV2", 68: "MotionLoop",
    69: "EyeBlink",
}


def name(value) -> str:
    """The command's name ("Cmd<value>" for a value the enum does not list)."""
    return COMMAND.get(value, f"Cmd{value}")
