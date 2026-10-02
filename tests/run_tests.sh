#!/usr/bin/env bash
# 运行全部自动化测试（不联网，使用本地假接口）
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
export PYTHONPATH="$ROOT/src:$HERE${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"
exec python3 -m unittest discover -s tests -p "test_*.py" -v "$@"
