# reco — agent 入口

## Agent skills

### Issue tracker

Issue 与 spec 托管在 GitHub Issues（kkkkifgtg/reco），用 `gh` CLI 读写；PR 与 issue 同走分诊状态机。见 `docs/agents/issue-tracker.md`。

### Triage labels

分诊角色映射：needs-triage / ready-for-agent / ready-for-human 为本仓库专属标签，needs-info→question、wontfix→wontfix 复用默认标签。见 `docs/agents/triage-labels.md`。

### Domain docs

single-context 布局：根 `CONTEXT.md` + `docs/adr/`。见 `docs/agents/domain.md`。
