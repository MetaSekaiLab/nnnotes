#!/usr/bin/env bash
# The open fonts the site's story text is drawn with (the files the published stories record in ui/fonts.json), into
# $1, each checked against its SHA-256: Noto Sans CJK 2.004 (ja and en: JP, zh-Hant: TC, zh-Hans: SC), Pretendard 1.3.9
# SemiBold (ko), Noto Color Emoji 2.051 (the emoji sprites). All under the SIL Open Font License 1.1.
set -euo pipefail
dir="$1"
mkdir -p "$dir"
cd "$dir"
cjk=https://github.com/notofonts/noto-cjk/raw/165c01b46ea533872e002e0785ff17e44f6d97d8/Sans/OTF
fetch() {  # fetch FILE URL SHA256
  if [ ! -f "$1" ] || ! echo "$3  $1" | sha256sum -c --quiet - 2>/dev/null; then
    curl -fsSL -o "$1" "$2"
    echo "$3  $1" | sha256sum -c -
  fi
}
fetch NotoSansCJKjp-Regular.otf "$cjk/Japanese/NotoSansCJKjp-Regular.otf" 68a3fc98800b2a27b371f2fb79991daf3633bd89309d4ffaa6946fd587f375b5
fetch NotoSansCJKtc-Regular.otf "$cjk/TraditionalChinese/NotoSansCJKtc-Regular.otf" dce08bd4fd91aa8aa76ed8fea4b694c2dfb8550f67871e326843212ddbeb88b4
fetch NotoSansCJKsc-Regular.otf "$cjk/SimplifiedChinese/NotoSansCJKsc-Regular.otf" 2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b
fetch NotoColorEmoji.ttf https://github.com/googlefonts/noto-emoji/raw/v2.051/fonts/NotoColorEmoji.ttf 72a635cb3d2f3524c51620cdde406b217204e8a6a06c6a096ff8ed4b5fd6e27b
if [ ! -f Pretendard-SemiBold.otf ] || ! echo "c89bc43027dc7cde5726e96223376f8eec09302b2fc1f8147fd5b57cfc376118  Pretendard-SemiBold.otf" | sha256sum -c --quiet - 2>/dev/null; then
  curl -fsSL -o pretendard.zip https://github.com/orioncactus/pretendard/releases/download/v1.3.9/Pretendard-1.3.9.zip
  echo "04be351a74d6bf7d60c480a3087e51d185485d35a52023142af1df19eb8c428a  pretendard.zip" | sha256sum -c -
  unzip -q -o -j pretendard.zip public/static/Pretendard-SemiBold.otf
  rm pretendard.zip
  echo "c89bc43027dc7cde5726e96223376f8eec09302b2fc1f8147fd5b57cfc376118  Pretendard-SemiBold.otf" | sha256sum -c -
fi
ls -l
