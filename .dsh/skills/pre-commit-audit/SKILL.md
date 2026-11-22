---
name: pre-commit-audit
description: 本仓库每次 git 提交前的独立第三方代码审计门禁。准备提交、写提交说明、开 PR、或需要给改动附审计证据时使用；发现 HIGH 级安全问题必须修复后再提交。审计工具：Bandit（Python SAST）、Semgrep（多语言 SAST）、OSV-Scanner（依赖漏洞）。
---

# 提交前第三方审计

每次 git 提交之前，先跑审计，再提交。审计由**独立第三方工具**完成，不是 agent 自审；agent 的职责是把结果读回来、拦下不合格改动、在提交说明里附证据。

## 三个审计工作流（从 77 个 GitHub 工作流中按本仓库技术栈选出）

本仓库是 Windows 上的 Python 桌面应用（PyQt6 + SQLite），无 Web API、无容器、无云基础设施，所以从 77 个候选中选出的三个是：

| 工作流 | 工具 | 覆盖 | 为什么选它 |
|---|---|---|---|
| **Bandit** | PyCQA/bandit-action@v1 | Python 源码 SAST | Python 专用、规则成熟、误报可基线化；提交前可本地跑 |
| **Semgrep** | pip 安装后扫 —— config auto | 跨语言 SAST + 密钥/注入类规则 | 规则库大，补 Bandit 覆盖不到的类别（命令行注入、反序列化等） |
| **OSV-Scanner** | google/osv-scanner | 依赖漏洞（含 wheels/ 里的第三方包） | 依赖面用 CVE 数据库审计，补 SAST 的盲区 |

候选里被排除的工具及原因（每次重选时按同样口径判断）：容器类（Trivy/Sysdig）、云 IaC 类（tfsec/Checkov/Prisma）、C/C++ 类（Flawfinder/Microsoft C++）、移动端类（MobSF/NowSecure）、已由平台提供且非独立的（CodeQL/Codacy 已在仓库，作补充而非替代）、需要登录或付费的（Snyk/Veracode/Checkmarx 等）。

## 流程（每次提交）

1. **跑本地审计**：`.venv/Scripts/python.exe scripts/audit.py`。该脚本调用 Bandit（HIGH 级失败，既有问题基线在 `.bandit-baseline.json`），并在 pip-audit 可用时审计依赖。工作树必须先激活审计门禁：`git config core.hooksPath .githooks`（仅首次）。
2. **读结论**：脚本退出码 0 = 通过；非 0 = 有 HIGH 级发现，**不提交**。修复后重跑；确有误报时在 `.bandit-baseline.json` 或 `# nosec` 注释里写明理由。
3. **提交并附证据**：提交说明末尾附 `审计：Bandit 通过（N 文件）`。若本轮有依赖变更，注明 OSV/Semgrep 的 CI 结论。
4. **推送后看 CI**：三个工作流会把结果写进 Code Scanning；红灯必须在合并前处理。

## 拦截规则

- Bandit HIGH（B6xx 等）：必须修复或基线化并写明理由。
- Semgrep ERROR 级：必须修复；WARNING 级可记录后放行。
- OSV：直接依赖的 HIGH/CRITICAL CVE 必须处理（升级/替换/记录豁免理由）。
- 无法修复的例外：写进提交说明与 PR 正文，不能静默绕过。

## 证据格式（提交说明模板）

```
<本次改动的一句话>

审计：Bandit 通过（packages/ scripts/ 共 N 文件，无 HIGH）
```

## 维护

- 新增依赖后重跑 OSV（CI）与 pip-audit（本地）。
- 基线文件 `.bandit-baseline.json` 只允许因历史遗留问题生成；新增问题不得进基线。
- 若技术栈变化（引入容器/云资源/移动端），按上面的口径从 77 个候选中重选三个，并同步更新本技能。
