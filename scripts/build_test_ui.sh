#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="$ROOT_DIR/webui/frontend"
BUILD_DIR="$ROOT_DIR/tmp_test/frontend-dev"
TARGET_DIR="$ROOT_DIR/tmp_test/drsai_ui/ui"

if [[ ! -d "$SOURCE_DIR/node_modules" ]]; then
  echo "[error] Frontend dependencies are missing: $SOURCE_DIR/node_modules" >&2
  exit 1
fi

mkdir -p "$BUILD_DIR"

# Build from a disposable directory so Gatsby clean/build never changes the
# production frontend's public directory.
rsync -a --delete \
  --exclude node_modules \
  --exclude public \
  --exclude .cache \
  "$SOURCE_DIR/" "$BUILD_DIR/"

ln -sfn "$SOURCE_DIR/node_modules" "$BUILD_DIR/node_modules"

pushd "$BUILD_DIR" >/dev/null
SERVICE_MODE=DEV GATSBY_SERVICE_MODE=DEV yarn build:dev
popd >/dev/null

mkdir -p "$TARGET_DIR"
rsync -a --delete "$BUILD_DIR/public/" "$TARGET_DIR/"

echo "DEV test UI built at: $TARGET_DIR"