#!/usr/bin/env bash
# pre-commit-guard.sh —— W14 retro 收编的确定性检查清单（机械性错误防线）。
# 用法：bash scripts/pre-commit-guard.sh（提交前手动跑；CI 可直接调用）
# 全部检查确定性可重放；任何一条失败即阻止提交。
set -uo pipefail
cd "$(dirname "$0")/.."
FAIL=0

say()  { printf '[guard] %s\n' "$*"; }
bail() { printf '[guard] FAIL: %s\n' "$*" >&2; FAIL=1; }

# 1. 分支断言：提交必须在预期分支（共享检出漂移防线，W6/W7 两度拦截）
EXPECTED_BRANCH="${1:-}"
CURRENT_BRANCH="$(git branch --show-current)"
if [ -n "$EXPECTED_BRANCH" ] && [ "$CURRENT_BRANCH" != "$EXPECTED_BRANCH" ]; then
  bail "分支漂移：在 $CURRENT_BRANCH 上，期望 $EXPECTED_BRANCH"
fi

# 2. add 清单=触碰文件全集：staged 与 working tree 不得有「漏 add 的已跟踪修改」
DIRTY_TRACKED="$(git status --porcelain=v1 | grep -c '^ M' || true)"
if [ "$DIRTY_TRACKED" -gt 0 ]; then
  say "提醒：$DIRTY_TRACKED 个已跟踪文件有未暂存改动（确认是否漏 add，队友文件需披露）"
  git status --porcelain=v1 | grep '^ M' | head -5
fi

# 3. .audit- 前缀禁入暂存区（auditor 专属命名空间）
AUDIT_STAGED="$(git diff --cached --name-only | grep -c '^\.audit-' || true)"
if [ "$AUDIT_STAGED" -gt 0 ]; then
  bail ".audit- 前缀文件入暂存区（auditor 专用命名空间）"
fi

# 4. 空表探针：py 文件不得有裸 except/pass 吞错（静默失败防线）
SILENT="$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$' | xargs -r grep -l 'except.*:$' 2>/dev/null | head -3)"
if [ -n "$SILENT" ]; then
  say "提醒：以下文件含裸 except，确认非吞错：$SILENT"
fi

# 5. 测试可收集：staged py 文件语法可编译（红测试 SyntaxError 防线，W14 PROBE 事故）
for f in $(git diff --cached --name-only --diff-filter=ACM | grep '\.py$'); do
  ./.venv/Scripts/python.exe -m py_compile "$f" 2>/dev/null || bail "语法错误：$f"
done

# 6. 变异核行为位：RV 脚本必须在位且引用真实源码路径（逆向验证防线）
if git diff --cached --name-only | grep -q 'test_.*\.py$'; then
  say "提醒：本票含测试改动——提交信息须注明逆向验证结果（转红项/字节级恢复）"
fi

# 7. 干净树回写：revert/恢复后 HEAD diff 必须为空（bytes 级回写防线）
if [ -f .scratch/reco-impl/notes/*reverse-verify*.py ] 2>/dev/null; then
  say "提醒：存在 RV 脚本——确认最后一次运行 RESTORED: ... passed + BYTE-IDENTICAL: True"
fi

if [ "$FAIL" -eq 0 ]; then
  say "ALL GUARDS PASSED"
else
  say "GUARD FAILURES PRESENT — 修复后再提交"
fi
exit $FAIL
