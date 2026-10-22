import numpy as np
import pytest

from nnnotes.packing.audio import AudioOptions, AudioTools, _compare, _pcm, probe_audio

TOOLS = AudioTools("ffmpeg", "ffprobe", "encoder", "decoder")


def test_pcm_crop_fades_and_gain_preserve_the_decoded_timeline(tmp_path, monkeypatch):
    source = np.full((4800, 2), 0.5, dtype="<f4")
    monkeypatch.setattr("nnnotes.packing.audio._run", lambda command: source.tobytes())
    options = AudioOptions(start_ms=10, duration_ms=50, fade_in_ms=5, fade_out_ms=10, gain_db=-6.020599913)
    result, rate = _pcm(tmp_path / "input.mp3", {"sampleRate": 48000, "channels": 2, "stream": 0}, options, TOOLS)
    assert rate == 48000 and result.shape == (2400, 2)
    assert result[0].tolist() == result[-1].tolist() == [0, 0]
    assert np.allclose(result[240:1900], 0.25)


@pytest.mark.parametrize("sample", [float("nan"), float("inf"), 1.5])
def test_invalid_or_clipping_pcm_is_rejected(tmp_path, monkeypatch, sample):
    monkeypatch.setattr("nnnotes.packing.audio._run", lambda command: np.full((48, 1), sample, dtype="<f4").tobytes())
    with pytest.raises(ValueError):
        _pcm(tmp_path / "input.wav", {"sampleRate": 48000, "channels": 1, "stream": 0}, AudioOptions(), TOOLS)


def test_decode_validation_detects_loudness_damage_swapped_channels_and_silence():
    positions = np.arange(48000)
    signal = np.column_stack([np.sin(positions * 0.1), np.sin(positions * 0.3)]).astype(np.float32) * 0.1
    assert _compare(signal, signal)["status"] == "passed"
    for broken in (signal * 2, signal[:, ::-1], signal * 0):
        with pytest.raises(ValueError, match="waveform"):
            _compare(signal, broken)
    assert _compare(signal * 0, signal * 0)["status"] == "passed"
    with pytest.raises(ValueError):
        _compare(signal, signal[:-1])


def test_audio_stream_selection_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "nnnotes.packing.audio._run",
        lambda command: (
            b"""{"streams": [
        {"index": 0, "codec_type": "video"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
        {"index": 2, "codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 1}]}"""
        ),
    )
    with pytest.raises(ValueError, match="selected"):
        probe_audio(tmp_path / "file.m4a", TOOLS)
    assert probe_audio(tmp_path / "file.m4a", TOOLS, stream=2)["channels"] == 1
