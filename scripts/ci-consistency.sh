#!/usr/bin/env bash
# 工单 13——RECO_PERF_TABS 快照比对入 CI（issue 13 验收 3；架构 §11.3/§13）。
# 用法：scripts/ci-consistency.sh
# ① 离屏生成 UI 遍历快照；② 与基线逐位比对（漂移即非零退出）；
# ③ 跑 CLI/GUI 逐位一致 + eval 门 + profile_rows 真实接线测试。
set -euo pipefail
cd "$(dirname "$0")/.."
PY=./.venv/Scripts/python.exe
WORK=tests/.perf-tabs-work
BASE=tests/golden/reco-perf-tabs.json
mkdir -p "$WORK" tests/golden

export QT_QPA_PLATFORM=offscreen
"$PY" tests/make_tabs_snapshot.py "$WORK/current.json"

if [ ! -s "$BASE" ]; then
  cp "$WORK/current.json" "$BASE"
  echo "baseline created (first run): $BASE"
fi

"$PY" - "$BASE" "$WORK/current.json" <<'EOF'
import json
import sys

base = json.loads(open(sys.argv[1], encoding="utf-8").read())
cur = json.loads(open(sys.argv[2], encoding="utf-8").read())
assert base == cur, f"RECO_PERF_TABS 快照漂移:\nbase={base}\ncurrent={cur}"
print("RECO_PERF_TABS snapshot: OK")
EOF

"$PY" -m pytest tests/test_consistency.py -q
