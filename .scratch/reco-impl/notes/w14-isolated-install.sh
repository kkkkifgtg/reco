#!/usr/bin/env bash
# W14 全验收清单第 2 项 v7：独立 venv + 全传递闭包 editable 预装（架构 L98 依赖方向）。
set -u
PY="$(pwd)/.venv/Scripts/python.exe"
WORK="$(mktemp -d)"
RESULT="$WORK/result.txt"
: > "$RESULT"
extra_deps() {
  case "$1" in
    reco-core)      echo "" ;;
    reco-labels)    echo "packages/reco-core" ;;
    reco-relations) echo "packages/reco-core" ;;
    reco-store)     echo "packages/reco-core" ;;
    reco-io)        echo "packages/reco-core packages/reco-store packages/reco-labels packages/reco-relations" ;;
    reco-cli)       echo "packages/reco-core packages/reco-store packages/reco-labels packages/reco-relations packages/reco-io" ;;
    reco-ui)        echo "packages/reco-core packages/reco-store packages/reco-labels packages/reco-relations packages/reco-io" ;;
  esac
}
for PKG in reco-core reco-labels reco-relations reco-store reco-io reco-cli reco-ui; do
  VENV="$WORK/$PKG"
  "$PY" -m venv "$VENV" >/dev/null 2>&1
  PIP="$VENV/Scripts/python.exe"
  "$PIP" -m pip install --quiet pytest >/dev/null 2>&1
  OK=0
  for DEP in $(extra_deps "$PKG"); do
    "$PIP" -m pip install --quiet -e "$DEP" >>"$WORK/$PKG.installout" 2>&1 || OK=1
  done
  "$PIP" -m pip install --quiet -e "packages/$PKG" >>"$WORK/$PKG.installout" 2>&1 || OK=1
  "$VENV/Scripts/python.exe" -m pytest "packages/$PKG/tests" -q --no-header -p no:cacheprovider >"$WORK/$PKG.testout" 2>&1
  TEST_RC=$?
  TESTS=$(tail -1 "$WORK/$PKG.testout")
  echo "$PKG deps_install=$OK test=$TEST_RC ($TESTS)" >> "$RESULT"
done
echo '=== 独立安装测试结果 v7（全传递闭包）==='
cat "$RESULT"
echo "WORK=$WORK"
