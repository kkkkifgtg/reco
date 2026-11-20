"""工单 10 逆向验证（E2）：5 项注入变异全部转红 → 恢复 → 字节级回绿。

变异选取（每项对应一条硬契约）：
  MV1 静音比例 0.05→0.5（降权仅 50%，A1「降权 95%+」失守）
  MV2 过期解除失效（过期窗口仍生效，A1「30/90 天过期自动解除」失守）
  MV3 惩罚信号反号（静音变加热，立即消失失守）
  MV4 探索位排除丢弃（静音候选被探索位捞回，「不重复出现」失守）
  MV5 画像折叠旁路（画像不更新，「秒级更新」失守）
"""
import hashlib
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[3]  # .scratch/reco-impl/notes/ → 仓库根
fb = root / "packages/reco-core/src/reco_core/feedback.py"
orig = fb.read_bytes()
orig_hash = hashlib.sha256(orig).hexdigest()

MUTATIONS = [
    ("MV1 mute ratio 0.05->0.5",
     "FEEDBACK_MUTE_RATIO = 0.05",
     "FEEDBACK_MUTE_RATIO = 0.5"),
    ("MV2 expiry lift disabled",
     "        if window is not None and window.with_remaining(now).remaining_days > 0.0:",
     "        if window is not None:"),
    ("MV3 penalty sign flipped",
     "                signals[bid] = -(_MUTE_DAMP - _MUTE_KEEP) * w_sim * sim / w_behavior_view",
     "                signals[bid] = (_MUTE_DAMP - _MUTE_KEEP) * w_sim * sim / w_behavior_view"),
    ("MV4 exploration exclusion dropped",
     "                      exploration_exclude=frozenset(muted_ids))  # 唯一入口（W8）",
     "                      exploration_exclude=frozenset())  # 唯一入口（W8）"),
    ("MV5 profile fold bypassed",
     "    new_profile = update_profile(profile, event, config, now=now_dt)  # 秒级更新（W9）",
     "    new_profile = profile  # 秒级更新（W9）"),
]

cwd = root / "packages/reco-core"
for name, old, new in MUTATIONS:
    text = orig.decode("utf-8")
    if old not in text:
        print(f"MUTATION {name}: TARGET NOT FOUND"); sys.exit(1)
    fb.write_text(text.replace(old, new), encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_feedback.py", "-q", "-x"],
                       cwd=cwd, capture_output=True, text=True, timeout=300)
    red = r.returncode != 0
    print(f"MUTATION {name}: {'RED' if red else 'NOT-RED!!'}")
    if not red:
        fb.write_bytes(orig); sys.exit(1)

fb.write_bytes(orig)
r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_feedback.py", "-q"],
                   cwd=cwd, capture_output=True, text=True, timeout=300)
tail = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else f"exit={r.returncode}"
print("RESTORED:", tail)
same = hashlib.sha256(fb.read_bytes()).hexdigest() == orig_hash
print("BYTE-IDENTICAL RESTORE:", same)
sys.exit(0 if (same and r.returncode == 0) else 1)
