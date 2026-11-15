#!/usr/bin/env bash
# Builds the installable Decky zip: out/RAT-<version>.zip (1.0.0 -> RAT-1.0.zip) containing RAT/{plugin.json, package.json, main.py,
# dist/index.js, py_modules/, bin/7zz, LICENSE, README.md}.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
VERSION=$(node -p "require('./package.json').version.replace(/\\.0$/, '')")
SEVENZIP_URL=https://github.com/ip7z/7zip/releases/download/25.01/7z2501-linux-x64.tar.xz
SEVENZIP_SHA256=4ca3b7c6f2f67866b92622818b58233dc70367be2f36b498eb0bdeaaa44b53f4

if [[ ! -x bin/7zz ]]; then
  echo "Fetching 7-Zip 25.01 (static build)..."
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/7z.tar.xz" "$SEVENZIP_URL"
  echo "$SEVENZIP_SHA256  $tmp/7z.tar.xz" | sha256sum -c --status || { echo "7-Zip checksum mismatch" >&2; exit 1; }
  tar -C "$tmp" -xf "$tmp/7z.tar.xz" 7zzs License.txt
  mkdir -p bin
  install -m 755 "$tmp/7zzs" bin/7zz
  cp "$tmp/License.txt" bin/7zz-License.txt
  rm -rf "$tmp"
fi

pnpm build
python3 -m unittest discover -s tests

STAGE=$(mktemp -d)
P="$STAGE/RAT"
mkdir -p "$P/dist" "$P/bin"
cp plugin.json package.json main.py LICENSE README.md "$P/"
mkdir -p "$P/assets" && cp assets/*.svg "$P/assets/" 2>/dev/null || true
cp dist/index.js "$P/dist/"
cp bin/7zz bin/7zz-License.txt "$P/bin/"
mkdir -p "$P/py_modules"
cp -r py_modules/rat "$P/py_modules/"
find "$P" -name "__pycache__" -type d -prune -exec rm -rf {} +
chmod 755 "$P/bin/7zz"
mkdir -p out
rm -f "out/RAT-$VERSION.zip"
(cd "$STAGE" && zip -qr -X "$ROOT/out/RAT-$VERSION.zip" RAT)
rm -rf "$STAGE"
(cd out && sha256sum "RAT-$VERSION.zip" > "RAT-$VERSION.zip.sha256")
echo "Built out/RAT-$VERSION.zip"
