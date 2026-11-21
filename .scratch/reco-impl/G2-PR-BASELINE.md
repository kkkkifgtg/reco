# G2 审计基线 — 真 PR 双闸门工作流就位报告

维护人：auditor ｜ 团队：reco-next ｜ 任务：t4 (G2) ｜ 日期：2026-11-21T12:25+0800 ｜ 状态：**就位（双闸门实弹验证）**

## 0. 就位结论

- gh CLI 2.105.0 凭据可用：`gh auth status` → 账号 **kkkkifgtg**（Active，keyring token，scopes 含 repo/workflow）。
- 远端 `origin = https://github.com/kkkkifgtg/reco.git`，PRIVATE，default branch = master；本地 master 5e179d4 = origin/master（ahead 0 / behind 0），共享检出净树（0 脏项）。
- 新 PR 流程**实弹验证通过**：本基线自身走完整闭环（push 分支 → gh pr create → test-runner+auditor 双结论 → gh pr merge → PR 链接留痕 audit-log 第 19 条）。
- 环境 gotcha：bash PATH 无 gh（`gh: command not found`），统一用全路径 `D:/Program Files/GitHub.cli/gh.exe`。

## 1. 门位清单（验证证据）

| 门位 | 命令 | 结果 |
|---|---|---|
| gh 凭据 | gh auth status | ✓ kkkkifgtg Active；scopes: gist/read:org/repo/workflow |
| 远端仓库 | gh repo view kkkkifgtg/reco --json | ✓ PRIVATE，defaultBranchRef=master，pushedAt 2026-11-21T04:13:18Z |
| 本地同步 | git rev-list --left-right --count master...origin/master | ✓ 0 0（fetch 后） |
| 净树 | git status --porcelain | ✓ 0 行 |
| PR 存量 | gh pr list --state all | 空（仓库零 PR 历史，本基线为第 1 张真 PR） |
| git 凭据 | git config credential.helper | 空（push 凭据走 gh 的 https 凭据管理） |

## 2. 每票标准审计流程（PR 化执行，承接 audit-checklist 常驻守护规则）

1. implementer status=completed + changedPaths → auditor 按 audit-checklist 读 diff（五类全查 + 该票必查项）。
2. 审计在隔离副本执行（git worktree / git archive 提取），零共享检出污染；仓库内临时副本禁用 `.audit-` 前缀，worktree 用完即 remove。
3. auditor 结论 = **pass** 或 **findings**（id / severity / problem / requiredFix / file:line）。
4. **双闸门**：PR 页面必须同时留下 test-runner 验收结论 + auditor 审计结论（`.github/PULL_REQUEST_TEMPLATE.md` 的 Audit Trail 区，两行非空）。
5. 双结论齐且为 pass → `gh pr merge <N> --squash`（或 `--merge`，按票声明）→ 分支删除 → **PR 链接 + 结论记入 .scratch/reco-impl/audit-log.md**（条目号回填 PR 模板区）。
6. findings → PR 不 merge，走 fix/<票号>-<finding-id> 分支：修复前红测试（先证红）→ 修复 → 逆向验证（revert 必转红）→ 重审 → merge（PR-first，禁止直改 master）。
7. blocker 未修复前，依赖它的下一批票不派发（audit-checklist 规则 3）。

## 3. 已验证命令模板（本基线实弹执行）

```bash
GH="D:/Program Files/GitHub.cli/gh.exe"          # bash PATH 无 gh，必须全路径
git push -u origin <branch>                       # 写前断言目标分支（既有纪律）
"$GH" pr create --base master --head <branch> --title "..." --body-file <body>
"$GH" pr view <N> --json url,state,mergeable,reviewDecision
"$GH" pr comment <N> --body "双闸门结论留痕"
"$GH" pr merge <N> --squash --delete-branch       # merge + 删分支 + 留 PR 链接
```

## 4. PR 模板双闸门留痕约定

- Audit Trail 区两行必须非空：test-runner 验收结论 / auditor 审计结论（audit-log 条目号）。
- Merge Danger：单向门/双向门 + 爆炸半径照填。
- merge 两条硬条件：① Audit Trail 双结论齐且为 pass；② 全量绿（test-runner 口径）。

## 5. 基线快照

- HEAD：5e179d4（chore: gh 授权辅助脚本收编）。
- audit-log：18 条全闭环（W1-W14+12b+W15/W16/W17/W18 + 修复票 W5-F/W6-A/W5-N + 双轴评审）；**第 19 条起为新流程条目**。
- 既有纪律延续：PR-first / 逆向验证 / 隔离副本审计 / 分支写操作断言 / `.audit-` 命名空间禁用 / worktree 用完即删。
- 收官备忘（不阻断，按第 18 条口径）：3 处 if False else 死分支、_last_status 多线程复核、isolated-install 升格 CI——用户指令另开团队处理，本票不新增改进项。

## 6. 本基线闭环（自我实证）

本文件经分支 chore/g2-pr-baseline → PR → 双结论 → merge 落 master；PR 链接见 audit-log 第 19 条（merge 前由 auditor 回填本节与 audit-log）。
