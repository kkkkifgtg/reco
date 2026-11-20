# W14 换核演练证据（swap-drill evidence）

> modules.md 唯一硬证据：只改 reco-core 内部，reco-cli/reco-ui 一行不改跑通全量。
> 演练分支：feat/14-swap-drill（基线 master 65a07b2，379 passed）。
> 变体快照 commit：1f463a8（完整 diff 永久存于 git 历史，本文件摘录关键面）。
> revert commit：见本文件末尾（变体撤销，全量回到基线绿）。

## 演练设计

| 项 | 原核 | 演练变体 |
|---|---|---|
| 度量 | 余弦相似度 dot/(‖q‖‖c‖) | 欧氏距离核 1/(1+d²)，d=到质心的平方欧氏距离 |
| 计算路径 | 点积/范数 | 逐维差平方和（质心键遍历 + 候选独有键惩罚） |
| 金标 | 0.9494740323391176（独立直算冻结） | 0.8333333333333334（同探针变体重冻结） |
| 零向量 | 0.0（余弦二义性） | 1/(1+‖质心‖²)=0.3787878787878788（诚实保序） |
| 保序性 | cosθ 单调 | 单位向量 1/(3−2cosθ) 严格单调增（证明：分母随 cos 增大而减小） |

## 演练步骤与结果

1. **换核**：`score.py:169 similarity_scores` 一处替换，导入面/签名/返回契约零改动
   （diff 摘录见下，完整版 `git show 1f463a8`）。
2. **cli/ui 零改动证明**：`git diff --stat HEAD -- packages/reco-cli packages/reco-ui` 输出为空
   （变体工作树上执行；`git status --porcelain` 仅 reco-core 两文件改动）。
3. **全量绿**：`pytest -q` → **379 passed**（变体工作树上）；CLI/GUI 逐位一致性测试
   `pytest packages/reco-ui/tests -k "consistency or rank"` → **3 passed**。
4. **红转绿明细**（换核后仅数值契约失败，结构性测试全过——证明测试有效性 E2）：
   - score 5 failed → 重冻结 SIM_GOLDEN（0.8333333333333334）+ 零向量/重合期望 → 33 passed
   - feedback 4 failed → 重冻结 TAIL（0.048525574959967135）/S2 恢复分（0.9705114991993427）
     /B8（0.95×0.6306019374818708）→ 22 passed
   - 结构性契约（排序稳定性/探索位确定性/静音降序沉底/weight_schedule/§6.5 隔离红线/
     CV 评估/画像折叠/重放）**零失败**——接口契约与数值契约成功解耦。
5. **revert**：`git revert 1f463a8`（见末尾）→ 全量 379 passed 回基线。

## 演练结论

- 模块边界（ADR-0001）成立：reco_core.score 的 similarity_scores 是唯一相似度接缝，
  替换实现不需要触碰任何消费方。
- 数值契约（金标断言）与结构契约（排序/状态/隔离）分层明确：换核只影响前者。
- feedback 层的静音惩罚通道（S=-0.9·w_sim·sim/w_behavior'）对新度量自动正确——
  它通过公开 similarity_scores 消费相似度，无度量假设内嵌。

## diff 摘录（完整版：git show 1f463a8）

```diff
--- a/packages/reco-core/src/reco_core/score.py
+++ b/packages/reco-core/src/reco_core/score.py
@@ -167,11 +167,14 @@
 def similarity_scores(candidate_vectors, positive_vectors, config=None):
-    """余弦相似度（手算金标 0.9494740323391176；无 sklearn，F5 安全）。"""
+    """欧氏距离核相似度【换核演练变体】sim = 1/(1+d²)（手算金标 0.8333333333333334）。"""
     ...
-    q_norm = math.sqrt(sum(w * w for w in centroid.values()))
     out = []
     for book_id, vec in candidate_vectors:
         dense = _as_dense(vec)
-        c_norm = math.sqrt(sum(w * w for w in dense.values()))
-        if q_norm <= 0.0 or c_norm <= 0.0:
-            out.append((book_id, 0.0))
-            continue
-        dot = sum(centroid[d] * dense[d] for d in centroid.keys() & dense.keys())
-        out.append((book_id, dot / (q_norm * c_norm)))
+        d2 = 0.0
+        for dim, cw in centroid.items():
+            diff = cw - dense.get(dim, 0.0)
+            d2 += diff * diff
+        for dim, vw in dense.items():
+            if dim not in centroid:
+                d2 += vw * vw
+        out.append((book_id, 1.0 / (1.0 + d2)))
     return out
```

测试契约重冻结：SIM_GOLDEN 0.9494740323391176 → 0.8333333333333334；
零向量 0.0 → 0.3787878787878788；重合 1.0 → 1.0（d²=0 核上界，语义不变）；
TAIL 0.0492403876506104 → 0.048525574959967135；
S2 恢复分 0.984807753012208 → 0.9705114991993427；
B8 0.95×0.7071067811865476 → 0.95×0.6306019374818708。
