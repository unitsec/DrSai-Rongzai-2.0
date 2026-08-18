#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUBLIC_DIR="$SCRIPT_DIR/public"
ROOT_ENV_FILE="$SCRIPT_DIR/../../.env"

# Keep command-line/exported variables higher priority than values from .env.
ORIG_DRSAI_UI_MODE="${DRSAI_UI_MODE-__UNSET__}"
ORIG_DRSAI_UI_STATIC_DIR="${DRSAI_UI_STATIC_DIR-__UNSET__}"
ORIG_DRSAI_UI_INTERNAL_STATIC_DIR="${DRSAI_UI_INTERNAL_STATIC_DIR-__UNSET__}"

if [[ -f "$ROOT_ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT_ENV_FILE"
  set +a
fi

if [[ "$ORIG_DRSAI_UI_MODE" != "__UNSET__" ]]; then
  DRSAI_UI_MODE="$ORIG_DRSAI_UI_MODE"
fi
if [[ "$ORIG_DRSAI_UI_STATIC_DIR" != "__UNSET__" ]]; then
  DRSAI_UI_STATIC_DIR="$ORIG_DRSAI_UI_STATIC_DIR"
fi
if [[ "$ORIG_DRSAI_UI_INTERNAL_STATIC_DIR" != "__UNSET__" ]]; then
  DRSAI_UI_INTERNAL_STATIC_DIR="$ORIG_DRSAI_UI_INTERNAL_STATIC_DIR"
fi

UI_MODE="${DRSAI_UI_MODE:-external}"
INTERNAL_STATIC_DEFAULT="$(cd "$SCRIPT_DIR/../../src/drsai_ui/ui_backend/backend/web/ui" 2>/dev/null && pwd || true)"

if [[ ! -d "$PUBLIC_DIR" ]]; then
  echo "[error] frontend public directory not found: $PUBLIC_DIR"
  echo "Run 'yarn build' first."
  exit 1
fi

TARGET_DIR="${DRSAI_UI_STATIC_DIR:-}"

if [[ -z "$TARGET_DIR" ]]; then
  if [[ "$UI_MODE" == "internal" ]]; then
    TARGET_DIR="${DRSAI_UI_INTERNAL_STATIC_DIR:-$INTERNAL_STATIC_DEFAULT}"
  else
    TARGET_DIR="$(python - <<'PY'
import importlib.util
from pathlib import Path

spec = importlib.util.find_spec('drsai_ui')
if spec is None or not spec.origin:
    raise SystemExit('')

pkg_dir = Path(spec.origin).resolve().parent
print(pkg_dir / 'ui_backend' / 'backend' / 'web' / 'ui')
PY
)"
  fi
fi

if [[ -z "$TARGET_DIR" ]]; then
  echo "[error] Cannot detect drsai_ui static directory in mode: $UI_MODE"
  echo "Set one of these manually:"
  echo "  DRSAI_UI_STATIC_DIR=/path/to/target yarn sync:ui"
  echo "  DRSAI_UI_INTERNAL_STATIC_DIR=/path/to/rongzai-agent/src/drsai_ui/ui_backend/backend/web/ui DRSAI_UI_MODE=internal yarn sync:ui"
  exit 1
fi

if [[ "$UI_MODE" == "internal" && ! -d "$TARGET_DIR" ]]; then
  echo "[error] Internal mode selected but static directory does not exist: $TARGET_DIR"
  echo "Expected internal drsai_ui source under rongzai-agent/src/drsai_ui"
  exit 1
fi

mkdir -p "$TARGET_DIR"

if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete "$PUBLIC_DIR/" "$TARGET_DIR/"
else
  rm -rf "$TARGET_DIR"/*
  cp -a "$PUBLIC_DIR"/. "$TARGET_DIR/"
fi

echo "Synced frontend static files to: $TARGET_DIR (mode=$UI_MODE)"
