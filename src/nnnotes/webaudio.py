"""The audio formats of a web build (`web --format`): per format the file suffix and the ffmpeg encoder flags (None:
the FLAC of the decode itself). Imports nothing, so the command line lists the formats without loading the
exporters."""

# BGM encodings for the web (ffmpeg). Chromium's decodeAudioData decodes each sample-aligned with the FLAC (encoder
# delay / pre-skip removed). Default AAC-LC 160 kbit/s in MP4: decodes in Safari / iOS as well.
WEB_AUDIO = {
    "aac": (".m4a", ["-c:a", "aac", "-b:a", "160k"]),
    "opus": (".opus", ["-c:a", "libopus", "-b:a", "128k"]),
    "vorbis": (".ogg", ["-c:a", "libvorbis", "-q:a", "5"]),
    "mp3": (".mp3", ["-c:a", "libmp3lame", "-b:a", "192k"]),
    "flac": (".flac", None),
}
DEFAULT_AUDIO_FORMAT = "aac"
