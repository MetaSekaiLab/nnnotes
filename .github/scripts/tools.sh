#!/usr/bin/env bash
# vgmstream-cli (the CRI HCA voices and music), pinned by version and SHA-256, into $1. ffmpeg comes from apt.
set -euo pipefail
dir="$1"
version=r2117
digest=2f98c77f756079f63fbd119939067f1ed461d77e70993bc4cc372736d859c84a
mkdir -p "$dir"
if [ ! -x "$dir/vgmstream-cli" ]; then
  curl -fsSL -o "$dir/vgmstream.zip" "https://github.com/vgmstream/vgmstream/releases/download/$version/vgmstream-linux.zip"
  echo "$digest  $dir/vgmstream.zip" | sha256sum -c -
  unzip -q -o "$dir/vgmstream.zip" vgmstream-cli -d "$dir"
  rm "$dir/vgmstream.zip"
  chmod 755 "$dir/vgmstream-cli"
fi
"$dir/vgmstream-cli" -V 2>/dev/null | head -1 || true
ffmpeg -version | head -1
