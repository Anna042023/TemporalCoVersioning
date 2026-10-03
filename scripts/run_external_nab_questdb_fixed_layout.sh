#!/usr/bin/env bash
set -euo pipefail
# Run the RQ11 fixed-layout execution-semantic check on a fresh QuestDB 10.0.1 root.
# Usage: scripts/run_external_nab_questdb_fixed_layout.sh /path/to/questdb-10.0.1-rt-linux-x86-64.tar.gz
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
INPUT="${1:-}"
if [[ -z "$INPUT" || ! -e "$INPUT" ]]; then
  echo "usage: $0 <questdb-runtime.tar.gz|extracted-runtime-dir>" >&2
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
WORK="${ROOT_DIR}/.questdb-nab-external"
RUNTIME="${WORK}/runtime"
QDBROOT="${WORK}/qdbroot"
TAG="tcv-nab-external"
rm -rf "$WORK"; mkdir -p "$RUNTIME" "$QDBROOT"
if [[ -d "$INPUT" ]]; then cp -a "$INPUT"/. "$RUNTIME"/; else tar -xzf "$INPUT" -C "$RUNTIME" --strip-components=1; fi
QDB_SH="$(find "$RUNTIME" -type f -path '*/bin/questdb.sh' -print -quit)"
[[ -n "$QDB_SH" ]] || { echo "QuestDB bin/questdb.sh not found" >&2; exit 2; }
chmod +x "$QDB_SH"
cleanup(){ "$QDB_SH" stop -t "$TAG" >/dev/null 2>&1 || true; }
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
python3 scripts/external_nab_questdb_fixed_layout.py

echo "RQ11 fixed-layout QuestDB semantic check complete; outputs are under data/reported/RQ11_questdb_fixed_layout_*"
