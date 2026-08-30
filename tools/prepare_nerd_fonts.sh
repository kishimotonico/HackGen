#!/usr/bin/env bash
set -euo pipefail

NERD_FONTS_VERSION="3.5.1"
HACK_ZIP_SHA256="fa24da7de7cefe7766614d27762570b20453c852fc1d5b657111666df9a5e449"
BASE_DIR=$(cd "$(dirname "$0")/.." && pwd)
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

archive="$TMP_DIR/Hack.zip"
extract_dir="$TMP_DIR/Hack"

curl --fail --location --retry 3 \
  --output "$archive" \
  "https://github.com/ryanoasis/nerd-fonts/releases/download/v${NERD_FONTS_VERSION}/Hack.zip"
printf '%s  %s\n' "$HACK_ZIP_SHA256" "$archive" | sha256sum --check -

mkdir -p "$extract_dir"
unzip -q "$archive" -d "$extract_dir"

for style in Regular Bold; do
  src="$extract_dir/HackNerdFont-${style}.ttf"
  test -f "$src"
  cp "$src" "$BASE_DIR/source/HackNerdFont-${style}.ttf"
done

python3 - "$BASE_DIR/hackgen_generator.sh" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
replacements = {
    "SelectMore(0uE5FA, 0uE6B7)": "SelectMore(0uE5FA, 0uE6FF)",
    "SelectMore(0uE700, 0uE8E3)": "SelectMore(0uE700, 0uE958)",
    "SelectMore(0uF300, 0uF381)": "SelectMore(0uF300, 0uF385)",
    "SelectMore(0uEA60, 0uEC1E)": "SelectMore(0uEA60, 0uEC84)",
}
for old, new in replacements.items():
    if new in text:
        continue
    if text.count(old) != 1:
        raise SystemExit(f"expected exactly one occurrence of {old!r}")
    text = text.replace(old, new)
path.write_text(text)
PY

echo "Prepared Hack Nerd Font source from Nerd Fonts v${NERD_FONTS_VERSION}"
