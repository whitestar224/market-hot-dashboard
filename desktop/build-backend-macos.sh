#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT/.pyinstaller-build"
DIST_DIR="$ROOT/dist-backend"

cd "$ROOT"
python3 -m pip install --upgrade pyinstaller
python3 -m pip install -r requirements.txt

rm -rf "$BUILD_DIR" "$DIST_DIR"

python3 -m PyInstaller \
  --clean \
  --noconfirm \
  --name xingyunshe-server \
  --distpath "$DIST_DIR" \
  --workpath "$BUILD_DIR" \
  --specpath "$BUILD_DIR" \
  server.py

echo "Backend executable built under $DIST_DIR"
