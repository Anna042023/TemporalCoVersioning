#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 /path/to/hsqldb-2.7.4.jar [output_dir]" >&2
  exit 2
fi
command -v javac >/dev/null 2>&1 || { echo "javac is required" >&2; exit 2; }
command -v java >/dev/null 2>&1 || { echo "java is required" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 2; }
JAR=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
OUTDIR=${2:-data/reported/hsqldb274}
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
case "$OUTDIR" in
  /*) ;;
  *) OUTDIR="$ROOT/$OUTDIR" ;;
esac
BUILD_DIR=$(mktemp -d "${TMPDIR:-/tmp}/tcv-hsqldb274.XXXXXX")
trap 'rm -rf "$BUILD_DIR"' EXIT
mkdir -p "$OUTDIR"
javac -cp "$JAR" -d "$BUILD_DIR" "$ROOT/scripts/HsqldbReplay.java"
java -cp "$BUILD_DIR:$JAR" HsqldbReplay "$OUTDIR"
python3 "$ROOT/scripts/summarize_hsqldb274.py" "$OUTDIR"
