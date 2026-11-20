# 工单 09（W9/t13）画像与置信度——口径 v1 与手算期望值（implementer-4，2026-11-21）

> 架构依据：§6.4 全部签名与失败模式、§17 Q1（分档 90/30/180）、§6.5 反例 1-5、CONTEXT.md 半衰期/置信度词条。
> 审计 09 必查：A4（权重来源版本化）、A5（只读数值化结果）、C2（重放逐位一致）、C4（w=0.5^(age/half)，非法配置拒绝回退+记事件）、C5（相对归一不饱和）、C6（n_pos<10 置信上限 0.3）、E3/E4（手算+独立来源）、E8（注入时钟）。
> W8 接缝（队长指令）：①WeightSet.version+reason 的事件归 W9 profile_updated（score 不写事件）；②【强制】evaluation_runs 持久化门 ≥10。
> 冻结来源：.scratch/reco-impl/notes/09-freeze-goldens.py（venv python stdlib 独立复算，E4）。

## 0. 口径 v1（PROFILE_VERSION = "reco-profile/1"）
1. **轴分类与半衰期**（Q1 分档，config["half_life_days"] 可部分覆盖）：tag（显式标注，含 mech）90d；signal（行为）30d；stable（题材 subject/关系 relation）180d。非法（≤0/非数值/非有限）→ 拒绝该配置、回退全默认并在画像事件 reason 记录（C4）。
2. **decay_weight(age_days, half_life_days) = 0.5**(age/half)**（黄金公式；w(half)=0.5；age<0 按 0；实现 exp(-ln2·age/half) 等价）。
3. **事件输入鸭子协议**：Event/dict 皆可；读 event_type/occurred_at/event_id/payload（dict 或 payload_json JSON）。reco_core 不 import reco_store（依赖方向）。
4. **折叠语义**：每事件产生 (axis, sign, w)：tag_hit → sign=+1，w=decay(class(axis))×confidence(默认1)；rec_feedback(kind=dislike) → sign=-1，w=decay×value(默认1)；signal_recorded 不进轴，进 behavior_signals[book_id]（见 8）。
   轴权 = Laplace 平滑 (sp+1)/(sp+sn+2)；每事件更新后 **单事件位移 clip |Δ|≤0.05**（防过拟合近期偏好；config["max_axis_step"] 可调，同样受配置校验回退）。
5. **profile_confidence**：value = cap × (0.4·样本因子 + 0.35·覆盖度 + 0.25·一致性)；样本因子=min(1,(n_pos+n_neg)/40)；覆盖度=有任一正或负折叠的轴数/总轴数（无轴=0）；一致性=1−|n_pos−n_neg|/(n_pos+n_neg)（无样本=0）；**cap=0.3 当 n_pos<10，否则 1.0**（C6）。display：n_pos<10 →「样本 n 篇，仅供参考，还不够准」；n_pos≥10 →「样本 n 篇」；n_pos=0 →「暂无正样本」。不展示 AUC。
6. **weights 三元组**（A4/接缝①）：build/update 接受鸭子 WeightSet（dict 或带 w_sim/w_model/w_behavior/version/retry/reason 属性）；缺省 None → (1.0, 0.0, 0.0, "weights/1", "小样本默认相似度")。原样进入 ProfileData.weights 与 profile_updated 事件 payload.weights_json（score 只读，profile 不写 score 内部，§6.5-1 反向成立）。
7. **版本与回放**（C2）：build → version=1；每次 update_profile → +1。verify_replay(events, stored, config)：用相同事件流重建，axis_weights 逐项 ==（同运算序列位级一致）、sample_counts/version/completeness/confidence.value 相等；浮点容差 1e-9 相对误差；差异进 diffs。
8. **行为信号**（§6.4 merge_signal）：dwell → strength=min(1, value×0.4/200)；completion≥0.9/reopen → 1；abandon/dislike → −1；再乘 decay(age, signal档30d) 得 behavior_signals[book_id]（正负可混，正值主导）；sample_counts["behavior"] 累加。
9. **事件缺口 → partial**：输入事件 event_id 序列出现跳跃（后一个 > 前一个+1）→ completeness="partial"（§6.4 失败模式），否则 "full"；event_id_high_water=max(event_id)。
10. **事件产出**：build/update 每次生成一条 profile_updated 事件 dict：payload ⊆ {axis_weights_json, sample_counts_json, top_tags_json, weights_json, half_life_days_json, reason, event_id_high_water, profile_version}（全在 W5 白名单内，B6）；occurred_at=now；config_fingerprint 来自 config（缺省 "profile-default"）。
11. **eval 持久化门（接缝②强制）**：persist_evaluation_gate(store, ...) 仅当 n_pos≥10 且 n_neg≥10 才调用 store.append_event("eval_run", ...)（鸭子 store）；否则不写、返回 None。store 由调用方注入，reco-core 零 reco-store import。
12. **axis_trends(profile_history)**：相邻版本逐轴差均值 → AxisTrend(axis, direction(↑/↓/→), delta_per_step)；幅度=末初差。

## 1. 手算黄金值（全部冻结自 09-freeze-goldens.py 实跑输出）
- S1 decay：w(60,90)=0.6299605249474366；w(15,90)=0.8908987181403393；w(15,30)=0.7071067811865476；w(90,180)=0.7071067811865476；w(half)=0.5（90/90、180/180、30/30 三档）；w(0)=1.0；w(270,90)=0.125；exp(-ln2·age/half) 与 0.5** 差=0.0；超大 age 不为负。
- S3 build（顺序折叠，clip=0.05）：subject.x 事件1（age30d，stable 180档，w=0.5^(30/180)=0.8908987181403393）→ 0.55（virgin clip 顶格）；事件2（age60d，w=0.5^(60/180)=0.7937005259840998）→ **0.7040868091555691**；plot.p（age90d，tag 90档，w=0.5）→ 0.55。top_tags 权重和 subject.x=1.684599244124439。weights 默认 (1,0,0)。
- S4 update clip：轴 (5,1) old=0.75；+tag_hit age60d（w=0.6299605249474366）→ target=0.768249229620644，|Δ|=0.0182<0.05 不剪裁 → **0.768249229620644**。virgin 轴 +1 事件 → 0.55；virgin 轴 dislike(w=1) → 0.45（clip 顶格）。
- S5 旧反馈更低（预热轴 (10,2)，base=0.7857142857142857）：newer(age30d)→0.7972109821521187 > older(age60d)→0.7949413469103821。
- S6 非法半衰期：{"tag":0,...} → 生效档回退默认 90/30/180；事件 reason 含「回退」。
- S7 置信度：C1=conf(3,0,1/3,1)=0.119；C2=conf(20,5,0.5,0.8)=0.625；C3=conf(1000,1000,1,1)=1.0；n_pos=20,n_neg=100,覆盖1,一致1 → cap=0.3 顶格；n_pos=10 → 1.0。
- S8 缺口：event_id 1,2,3,5 → completeness="partial"，high_water=5。
- S9 重放：同流两次 build → axis_weights dict == 逐位相等；与 stored dict 容差 1e-9 一致。
- S10 负反馈序列（8×dislike，每条 w=0.5 即「半个半衰期前」的反馈；virgin 轴）：0.45, 0.35, 0.2857142857142857, 0.25, 0.2222222222222222, 0.2, 0.18181818181818182, 0.16666666666666666 —— 严格递减且恒 >0（C7 不硬排除、趋零不归零）。
- S11 trends：[0.30,0.35,0.40,0.45] → ↑ delta_per_step=0.05000000000000001；逆序 → ↓ −0.05。
- S12 行为：dwell500（age0）→ behavior=1.0；dwell100（age0）→ 0.2。
- S13 权重接缝：传入 {w_sim:0.7133001217509216, w_model:0.03669987824907843, w_behavior:0.25, version:"weights/1", reason:"n_pos=20,n_neg=15,cv_auc=0.7,cap=0.5"} → ProfileData.weights 与 profile_updated payload.weights_json 原样回声。
- S14 eval 门：n_pos=12,n_neg=11 → 写；n_pos=9 → 不写（append_event 零调用）。

## 2. 公开签名（reco_core.profile，不改动包 __init__，沿用 score.py 的模块级接缝先例）
```python
PROFILE_VERSION = "reco-profile/1"
DEFAULT_HALF_LIFE_DAYS = {"tag": 90, "signal": 30, "stable": 180}
class InvalidHalfLife(ValueError)
@dataclass Confidence: value/n_pos/n_neg/display
@dataclass AxisTrend: axis/direction/delta_per_step
@dataclass ProfileData: version/updated_at/axis_weights/sample_counts/top_tags/weights/weight_version/weight_reason/half_life_days/behavior_signals/completeness/event_id_high_water/confidence/generated_events
decay_weight(age_days, half_life_days) -> float
build_profile(events, config=None, now=None, weight_set=None) -> ProfileData
update_profile(profile, event, config=None, now=None, weight_set=None) -> ProfileData
profile_confidence(profile) -> Confidence
axis_trends(profile_history) -> list[AxisTrend]
export_profile(profile) -> dict
verify_replay(events, stored, config=None) -> ReplayReport(ok, diffs)   # reco_core 侧自有 ReplayReport
merge_signal(event, config=None, now=None) -> list[dict]
persist_evaluation_gate(store, *, method, n_pos, n_neg, folds, auc, ci_low, ci_high, now, config_fingerprint, notes="") -> int | None
```
