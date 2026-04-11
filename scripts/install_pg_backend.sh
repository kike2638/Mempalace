#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/skuznetsov/pg_sorted_heap.git"
BUILD_DIR="${TMPDIR:-/tmp}/mempalace-pg-sorted-heap"
PG_CONFIG_BIN="${PG_CONFIG:-}"
PSQL_BIN="${PSQL:-}"
DSN="${MEMPALACE_POSTGRES_DSN:-}"
SOURCE_DIR=""
USE_SUDO=0
FORCE_BUILD=0
CREATE_EXTENSION=1

usage() {
  cat <<'EOF'
Install pg_sorted_heap for the MemPalace PostgreSQL backend.

Usage:
  scripts/install_pg_backend.sh [options]

Options:
  --dsn DSN             PostgreSQL DSN where CREATE EXTENSION should run.
  --pg-config PATH      pg_config for the PostgreSQL version to target.
  --psql PATH           psql binary to use for CREATE EXTENSION.
  --source DIR          Build from an existing pg_sorted_heap checkout.
  --repo URL            Git repo to clone when --source is not supplied.
  --build-dir DIR       Clone/build directory. Default: $TMPDIR/mempalace-pg-sorted-heap.
  --sudo                Run make install through sudo.
  --no-create-extension Install files only; do not run CREATE EXTENSION.
  --force-build         Build/install even if pg_sorted_heap already appears installed.
  -h, --help            Show this help.

Environment:
  PG_CONFIG                  Alternative way to set --pg-config.
  PSQL                       Alternative way to set --psql.
  MEMPALACE_POSTGRES_DSN     Alternative way to set --dsn.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dsn)
      DSN="${2:?--dsn requires a value}"
      shift 2
      ;;
    --pg-config)
      PG_CONFIG_BIN="${2:?--pg-config requires a value}"
      shift 2
      ;;
    --psql)
      PSQL_BIN="${2:?--psql requires a value}"
      shift 2
      ;;
    --source)
      SOURCE_DIR="${2:?--source requires a value}"
      shift 2
      ;;
    --repo)
      REPO_URL="${2:?--repo requires a value}"
      shift 2
      ;;
    --build-dir)
      BUILD_DIR="${2:?--build-dir requires a value}"
      shift 2
      ;;
    --sudo)
      USE_SUDO=1
      shift
      ;;
    --no-create-extension)
      CREATE_EXTENSION=0
      shift
      ;;
    --force-build)
      FORCE_BUILD=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

if [[ -z "$PG_CONFIG_BIN" ]]; then
  PG_CONFIG_BIN="$(command -v pg_config || true)"
fi
if [[ -z "$PG_CONFIG_BIN" || ! -x "$PG_CONFIG_BIN" ]]; then
  echo "Could not find pg_config. Pass --pg-config /path/to/pg_config." >&2
  exit 1
fi

if [[ -z "$PSQL_BIN" ]]; then
  PSQL_BIN="$(command -v psql || true)"
fi

SHARE_DIR="$("$PG_CONFIG_BIN" --sharedir)"
PKG_LIB_DIR="$("$PG_CONFIG_BIN" --pkglibdir)"
CONTROL_FILE="$SHARE_DIR/extension/pg_sorted_heap.control"
case "$(uname -s)" in
  Darwin) LIB_SUFFIX=".dylib" ;;
  *) LIB_SUFFIX=".so" ;;
esac
LIB_FILE="$PKG_LIB_DIR/pg_sorted_heap$LIB_SUFFIX"

echo "Target PostgreSQL: $("$PG_CONFIG_BIN" --version)"
echo "pg_config: $PG_CONFIG_BIN"
echo "extension dir: $SHARE_DIR/extension"
echo "library dir: $PKG_LIB_DIR"

if [[ "$FORCE_BUILD" -eq 0 && -f "$CONTROL_FILE" && -e "$LIB_FILE" ]]; then
  echo "pg_sorted_heap already appears installed for this PostgreSQL."
else
  need_cmd make
  if [[ -n "$SOURCE_DIR" ]]; then
    if [[ ! -f "$SOURCE_DIR/Makefile" ]]; then
      echo "--source does not look like a pg_sorted_heap checkout: $SOURCE_DIR" >&2
      exit 1
    fi
    BUILD_PATH="$SOURCE_DIR"
  else
    need_cmd git
    BUILD_PATH="$BUILD_DIR"
    if [[ -d "$BUILD_PATH/.git" ]]; then
      echo "Updating existing checkout: $BUILD_PATH"
      git -C "$BUILD_PATH" pull --ff-only
    elif [[ -e "$BUILD_PATH" ]]; then
      echo "Build directory exists but is not a git checkout: $BUILD_PATH" >&2
      echo "Pass --build-dir to use another directory, or remove it manually." >&2
      exit 1
    else
      echo "Cloning $REPO_URL into $BUILD_PATH"
      git clone --depth 1 "$REPO_URL" "$BUILD_PATH"
    fi
  fi

  echo "Building pg_sorted_heap"
  make -C "$BUILD_PATH" PG_CONFIG="$PG_CONFIG_BIN"

  echo "Installing pg_sorted_heap"
  if [[ "$USE_SUDO" -eq 1 ]]; then
    sudo make -C "$BUILD_PATH" install PG_CONFIG="$PG_CONFIG_BIN"
  else
    make -C "$BUILD_PATH" install PG_CONFIG="$PG_CONFIG_BIN"
  fi

  if [[ ! -f "$CONTROL_FILE" || ! -e "$LIB_FILE" ]]; then
    echo "Install finished, but expected files were not found." >&2
    echo "Missing? control=$CONTROL_FILE lib=$LIB_FILE" >&2
    exit 1
  fi
fi

if [[ "$CREATE_EXTENSION" -eq 1 ]]; then
  if [[ -z "$DSN" ]]; then
    echo "No DSN supplied; skipping CREATE EXTENSION."
    echo "Run later: psql <dsn> -c 'CREATE EXTENSION IF NOT EXISTS pg_sorted_heap;'"
  else
    if [[ -z "$PSQL_BIN" || ! -x "$PSQL_BIN" ]]; then
      echo "Could not find psql. Pass --psql /path/to/psql or use --no-create-extension." >&2
      exit 1
    fi
    echo "Creating extension in target database"
    "$PSQL_BIN" "$DSN" -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS pg_sorted_heap;"
    "$PSQL_BIN" "$DSN" -v ON_ERROR_STOP=1 -Atc \
      "SELECT extname FROM pg_extension WHERE extname = 'pg_sorted_heap';"
  fi
fi

cat <<'EOF'

MemPalace PostgreSQL backend environment:
  export MEMPALACE_BACKEND=postgres
  export MEMPALACE_POSTGRES_DSN="<your postgres dsn>"
EOF
