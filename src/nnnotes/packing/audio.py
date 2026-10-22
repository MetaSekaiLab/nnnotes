"""Common audio inputs to validated, finite, single-cue ACB files."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ..formats import acbwrite, crikey, hca
from ..source.config import Config, tool

__all__ = ["AudioOptions", "AudioResult", "AudioTools", "build_audio", "probe_audio"]


@dataclass(frozen=True)
class AudioTools:
    """External tools; VGAudioCli can be an executable or a .NET DLL."""

    ffmpeg: str
    ffprobe: str
    vgaudio: str
    vgmstream: str

    @classmethod
    def configured(cls, config: Config | None = None) -> AudioTools:
        def select(name, executable):
            path = config.path("paths", name) if config else None
            return str(path) if path else tool(name, executable)

        return cls(
            select("ffmpeg", "ffmpeg"),
            select("ffprobe", "ffprobe"),
            select("vgaudio", "VGAudioCli"),
            select("vgmstream", "vgmstream-cli"),
        )

    def fingerprints(self) -> dict:
        output = {}
        for name, value in asdict(self).items():
            path = Path(shutil.which(value) or value)
            if not path.is_file():
                raise ValueError(f"audio tool not found: {name}")
            output[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return output


@dataclass(frozen=True)
class AudioOptions:
    """PCM processing. All time values refer to the decoded source timeline."""

    sample_rate: int | None = None
    channels: int | None = None
    stream: int | None = None
    gain_db: float = 0
    start_ms: int = 0
    duration_ms: int | None = None
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    quality: str = "high"

    def __post_init__(self):
        for name in ("start_ms", "duration_ms", "fade_in_ms", "fade_out_ms"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.duration_ms == 0 or not math.isfinite(self.gain_db) or not -120 <= self.gain_db <= 24:
            raise ValueError("audio duration must be positive and gain_db must be in -120..24")
        if (
            isinstance(self.channels, bool)
            or self.channels not in (None, 1, 2)
            or self.sample_rate not in (None, 44100, 48000)
        ):
            raise ValueError("audio output supports mono/stereo at 44100 or 48000 Hz")
        if self.stream is not None and (
            isinstance(self.stream, bool) or not isinstance(self.stream, int) or self.stream < 0
        ):
            raise ValueError("stream must be a nonnegative integer")
        if self.quality not in ("highest", "high", "middle", "low", "lowest"):
            raise ValueError("unsupported HCA quality")


@dataclass(frozen=True)
class AudioResult:
    """ACB bytes and a portable verification report."""

    data: bytes
    report: dict


def _run(command: list[str]) -> bytes:
    try:
        process = subprocess.run(command, capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"audio tool failed: {Path(command[0]).name}") from error
    if process.returncode:
        message = process.stderr.decode("utf-8", "replace")[-2000:]
        raise ValueError(f"{Path(command[0]).name} failed: {message}")
    return process.stdout


def probe_audio(source: Path, tools: AudioTools, *, stream: int | None = None) -> dict:
    """Probe actual audio streams, requiring explicit selection when there are several."""
    raw = _run([tools.ffprobe, "-v", "error", "-show_streams", "-of", "json", str(source)])
    streams = [item for item in json.loads(raw)["streams"] if item.get("codec_type") == "audio"]
    selected = [item for item in streams if item["index"] == stream] if stream is not None else streams
    if len(selected) != 1:
        raise ValueError("input must have exactly one selected audio stream; set stream to its index")
    audio = selected[0]
    return {
        "stream": audio["index"],
        "codec": audio["codec_name"],
        "sampleRate": int(audio["sample_rate"]),
        "channels": int(audio["channels"]),
    }


def _sample_time(milliseconds: int, rate: int) -> int:
    return (milliseconds * rate + 500) // 1000


def _pcm(source: Path, info: dict, options: AudioOptions, tools: AudioTools) -> tuple[np.ndarray, int]:
    rate = options.sample_rate or info["sampleRate"]
    channels = options.channels or info["channels"]
    if rate not in (44100, 48000) or channels not in (1, 2):
        raise ValueError("set sample_rate/channels to convert this input to mono/stereo at 44100 or 48000 Hz")
    raw = _run(
        [
            tools.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-err_detect",
            "explode",
            "-i",
            str(source),
            "-map",
            f"0:{info['stream']}",
            "-vn",
            "-sn",
            "-dn",
            "-ar",
            str(rate),
            "-ac",
            str(channels),
            "-c:a",
            "pcm_f32le",
            "-f",
            "f32le",
            "pipe:1",
        ]
    )
    if not raw or len(raw) % (4 * channels):
        raise ValueError("audio decoder produced empty or incomplete PCM")
    samples = np.frombuffer(raw, dtype="<f4").reshape(-1, channels).copy()
    if not np.all(np.isfinite(samples)):
        raise ValueError("audio contains non-finite PCM samples")
    start = _sample_time(options.start_ms, rate)
    end = start + _sample_time(options.duration_ms, rate) if options.duration_ms is not None else len(samples)
    if not 0 <= start < end <= len(samples):
        raise ValueError("audio crop is empty or outside the decoded sample range")
    samples = samples[start:end].copy()
    samples *= 10 ** (options.gain_db / 20)
    fade_in, fade_out = (_sample_time(value, rate) for value in (options.fade_in_ms, options.fade_out_ms))
    if fade_in + fade_out > len(samples):
        raise ValueError("audio fades exceed the selected sample range")
    if fade_in:
        samples[:fade_in] *= np.linspace(0, 1, fade_in, dtype=np.float32)[:, None]
    if fade_out:
        samples[-fade_out:] *= np.linspace(1, 0, fade_out, dtype=np.float32)[:, None]
    if np.max(samples) > 1 or np.min(samples) < -1:
        raise ValueError("PCM would clip; lower gain_db")
    return samples, rate


def _statistics(pcm: np.ndarray) -> dict:
    return {
        "peak": float(np.max(np.abs(pcm))),
        "rms": [float(value) for value in np.sqrt(np.mean(np.square(pcm, dtype=np.float64), axis=0))],
    }


def _compare(reference: np.ndarray, decoded: np.ndarray) -> dict:
    if decoded.shape != reference.shape:
        raise ValueError("ACB decoded sample count/channels do not match the processed PCM")
    energy = np.mean(np.square(reference, dtype=np.float64), axis=0)
    errors = np.mean(np.square(decoded.astype(np.float64) - reference), axis=0)
    active = energy > 1e-8
    ratios = np.zeros_like(energy)
    ratios[active] = errors[active] / energy[active]
    decoded_energy = np.mean(np.square(decoded, dtype=np.float64), axis=0)
    gain = np.ones_like(energy)
    gain[active] = decoded_energy[active] / energy[active]
    if (
        np.any(ratios[active] > 0.05)
        or np.any(errors[~active] > 1e-6)
        or np.any(gain[active] > 10**0.1)
        or np.any(gain[active] < 10**-0.1)
    ):
        raise ValueError("ACB decoded waveform differs substantially from the processed PCM")
    return {
        "status": "passed",
        "relativeMeanSquareError": ratios.tolist(),
        "powerRatio": gain.tolist(),
        "decoded": _statistics(decoded),
        "samples": len(decoded),
    }


def build_audio(
    source: Path,
    template: bytes,
    *,
    cue_name: str,
    sheet_name: str | None = None,
    options: AudioOptions | None = None,
    tools: AudioTools | None = None,
    cipher_type: int = 0,
    key: int | None = None,
) -> AudioResult:
    """Decode common audio, encode HCA, build ACB and validate using an independent decoder.

    The key is used only in memory and an isolated temporary decoder key file. Reports contain no key material.
    FFmpeg decodes the source; VGAudioCli encodes HCA; vgmstream verifies the resulting ACB.
    """
    source = Path(source)
    options, tools = options or AudioOptions(), tools or AudioTools.configured()
    if cipher_type not in (0, 56) or (cipher_type == 56 and not key):
        raise ValueError("audio cipher must be 0 or 56 with a configured nonzero key")
    identities = tools.fingerprints()
    source_data = source.read_bytes()
    info = probe_audio(source, tools, stream=options.stream)
    pcm, rate = _pcm(source, info, options, tools)
    quantized = np.clip(np.rint(pcm.astype(np.float64) * 32768), -32768, 32767).astype("<i2")
    reference = quantized.astype(np.float32) / 32768
    with tempfile.TemporaryDirectory(prefix="nnnotes-audio-") as directory:
        root = Path(directory)
        wav = root / "input.wav"
        with wave.open(str(wav), "wb") as output:
            output.setparams((pcm.shape[1], 2, rate, len(pcm), "NONE", "not compressed"))
            output.writeframes(quantized.tobytes())
        encoder = [tools.vgaudio]
        if Path(tools.vgaudio).suffix.lower() == ".dll":
            encoder.insert(0, shutil.which("dotnet") or "dotnet")
        _run([*encoder, str(wav), str(root / "audio.hca"), "--no-loop", "--hcaquality", options.quality])
        encoded = (root / "audio.hca").read_bytes()
        metadata = hca.inspect(encoded)
        if (metadata["channels"], metadata["sampleRate"], metadata["samples"], metadata["cipherType"]) != (
            pcm.shape[1],
            rate,
            len(pcm),
            0,
        ):
            raise ValueError("HCA encoder output does not match the requested PCM dimensions/cipher")
        stream_data = hca.crypt(encoded, key) if cipher_type == 56 and key else encoded
        if cipher_type == 56 and key and hca.crypt(stream_data, key, decrypt=True) != encoded:
            raise ValueError("HCA encryption roundtrip failed")
        data, bank_report = acbwrite.author(
            template, stream_data, cue_name=cue_name, sheet_name=sheet_name, allow_encrypted_hca=cipher_type == 56
        )
        from ..formats.acb import tables

        head, _ = tables(data)
        bank = head["AwbFile"]
        if stream_data not in bank:
            raise ValueError("ACB did not preserve the encoded HCA")
        acb_path = root / "output.acb"
        acb_path.write_bytes(data)
        if cipher_type == 56 and key:
            crikey.write_hcakey(key, root)
        _run([tools.vgmstream, "-i", "-o", str(root / "decoded.wav"), str(acb_path)])
        with wave.open(str(root / "decoded.wav"), "rb") as decoded:
            if decoded.getsampwidth() != 2 or decoded.getframerate() != rate:
                raise ValueError("ACB decoder output format does not match the encoded audio")
            actual = np.frombuffer(decoded.readframes(decoded.getnframes()), dtype="<i2")
            actual = actual.reshape(-1, decoded.getnchannels()).astype(np.float32) / 32768
        validation = _compare(reference, actual)
    if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(source_data).digest():
        raise ValueError("audio source changed during conversion")
    return AudioResult(
        data,
        {
            "format": "nnnotes.audio-build/1",
            "source": info,
            "sourceSha256": hashlib.sha256(source_data).hexdigest(),
            "options": asdict(options),
            "tools": identities,
            "processed": {"samples": len(pcm), "sampleRate": rate, "channels": pcm.shape[1], **_statistics(reference)},
            "hca": hca.inspect(stream_data),
            "bank": bank_report,
            "validation": {"structure": "passed", "decode": validation},
            "acbSha256": hashlib.sha256(data).hexdigest(),
        },
    )
