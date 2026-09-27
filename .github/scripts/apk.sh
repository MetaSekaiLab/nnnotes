#!/usr/bin/env bash
# The game's split APKs (base.apk; split_config.arm64_v8a.apk holds the CRI Lips library) into $1, with playfetch
# ($PLAYFETCH_VERSION) and the account store in the secret PLAYFETCH_CREDENTIALS (the credentials.json of
# `playfetch login`). Google Play serves the current version; nnnotes stops, naming the class, when its type trees do not
# match the APK's Unity version.
set -euo pipefail
dir="$1"
if [ -z "${PLAYFETCH_CREDENTIALS_JSON:-}" ]; then
  echo "::error::set the repository secret PLAYFETCH_CREDENTIALS (the credentials.json of playfetch login)"
  exit 1
fi
bin="$RUNNER_TEMP/playfetch"
curl -fsSL -o "$bin" "https://github.com/Exmeaning/playfetch/releases/download/$PLAYFETCH_VERSION/playfetch-$PLAYFETCH_VERSION-linux-amd64"
curl -fsSL -o "$RUNNER_TEMP/SHA256SUMS" "https://github.com/Exmeaning/playfetch/releases/download/$PLAYFETCH_VERSION/SHA256SUMS"
(cd "$RUNNER_TEMP" && grep " playfetch-$PLAYFETCH_VERSION-linux-amd64\$" SHA256SUMS | sed "s| playfetch-$PLAYFETCH_VERSION-linux-amd64| playfetch|" | sha256sum -c -)
chmod 755 "$bin"
export PLAYFETCH_CREDENTIALS="$RUNNER_TEMP/playfetch-credentials.json"
umask 077
printf '%s' "$PLAYFETCH_CREDENTIALS_JSON" > "$PLAYFETCH_CREDENTIALS"
"$bin" pull "$APK_PACKAGE" -out-root "$RUNNER_TEMP/apk-downloads" -mode split
got="$(find "$RUNNER_TEMP/apk-downloads/$APK_PACKAGE" -name base.apk | sort | tail -1)"
[ -n "$got" ] || { echo "::error::playfetch pulled no base.apk"; exit 1; }
mkdir -p "$dir"
cp "$(dirname "$got")"/*.apk "$dir/"
rm -f "$PLAYFETCH_CREDENTIALS"
ls -l "$dir"
