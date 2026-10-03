#!/usr/bin/env bash
set -euo pipefail

# One-command QuestDB 10.0.1 native replay.
# Usage:
#   scripts/run_native_questdb_local.sh /path/to/questdb-10.0.1-rt-linux-x86-64.tar.gz
# or
#   scripts/run_native_questdb_local.sh /path/to/extracted/questdb-10.0.1-rt-linux-x86-64

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
INPUT="${1:-}"
if [[ -z "$INPUT" ]]; then
  echo "usage: $0 <questdb-runtime.tar.gz|extracted-runtime-dir>" >&2
  exit 2
fi
if [[ ! -e "$INPUT" ]]; then
  echo "QuestDB runtime not found: $INPUT" >&2
  exit 2
fi
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 2; }
command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 2; }
if [[ -f "$INPUT" ]]; then
  command -v tar >/dev/null 2>&1 || { echo "tar is required for a runtime archive" >&2; exit 2; }
fi
INPUT="$(python3 - "$INPUT" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
)"
WORK="${ROOT_DIR}/.questdb-native-run"
RUNTIME="${WORK}/runtime"
QDBROOT="${WORK}/qdbroot"
TAG="tcv-native-replay"

rm -rf "$WORK"
mkdir -p "$RUNTIME" "$QDBROOT"

if [[ -d "$INPUT" ]]; then
  cp -a "$INPUT"/. "$RUNTIME"/
elif [[ -f "$INPUT" ]]; then
  tar -xzf "$INPUT" -C "$RUNTIME" --strip-components=1
else
  echo "QuestDB runtime not found: $INPUT" >&2
  exit 2
fi

QDB_SH="$(find "$RUNTIME" -type f -path '*/bin/questdb.sh' -print -quit)"
if [[ -z "$QDB_SH" ]]; then
  echo "Could not locate bin/questdb.sh under $RUNTIME" >&2
  exit 2
fi
chmod +x "$QDB_SH"

cleanup() {
  "$QDB_SH" stop -t "$TAG" >/dev/null 2>&1 || true
}
trap cleanup EXIT

"$QDB_SH" start -d "$QDBROOT" -t "$TAG"

healthy=0
for ((i=0; i<90; i++)); do
  if curl -fsS http://127.0.0.1:9003/status >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 1
done
if [[ "$healthy" -ne 1 ]]; then
  echo "QuestDB did not become healthy within 90 seconds." >&2
  exit 1
fi

cd "$ROOT_DIR"
python3 scripts/native_questdb_replay.py --dry-run
python3 scripts/native_questdb_replay.py \
  --host http://127.0.0.1:9000 \
  --reps 4 \
  --warmup 600 \
  --migration-reps 15 \
  --concurrent-readers 4

echo "Native replay complete. RQ10_questdb_* outputs are under ${ROOT_DIR}/data/reported/."
