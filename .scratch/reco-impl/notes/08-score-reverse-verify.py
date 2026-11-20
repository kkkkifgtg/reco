"""工单 08 逆向验证（E2）：4 项注入变异全部转红 → 恢复 → 字节级回绿。

变异选取（每项对应一条硬契约）：
  M1 去同分稳定次序（book_id 决胜）      → S1 排序稳定性转红
  M2 去权重归一断言                      → S3a「报错而非继续」转红
  M3 探索位选取返回空集                  → S1 探索位存在性/确定性转红
  M4 余弦金标偏移                        → S4 手算期望（禁自洽）转红
"""
import hashlib
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[3]  # .scratch/reco-impl/notes/ → 仓库根
score = root / "packages/reco-core/src/reco_core/score.py"
orig = score.read_bytes()
orig_hash = hashlib.sha256(orig).hexdigest()

MUTATIONS = [
    ("M1 tie-break removed",
     "ordered = sorted(rows, key=lambda r: (-r[1], r[0]))",
     "ordered = sorted(rows, key=lambda r: (-r[1],))"),
    ("M2 norm assertion removed",
     "    if abs(total - 1.0) > _NORM_TOL:",
     "    if False:"),
    ("M3 exploration picks empty set",
     "    return {pool[(start + i) % len(pool)] for i in range(count)}",
     "    return set()"),
    ("M4 cosine golden shifted",
     "        out.append((book_id, dot / (q_norm * c_norm)))",
     "        out.append((book_id, dot / (q_norm * c_norm) + 0.001))"),
]

cwd = root / "packages/reco-core"
for name, old, new in MUTATIONS:
    text = orig.decode("utf-8")
    if old not in text:
        print(f"MUTATION {name}: TARGET NOT FOUND"); sys.exit(1)
    score.write_text(text.replace(old, new), encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_score.py", "-q", "-x"],
                       cwd=cwd, capture_output=True, text=True, timeout=300)
    red = r.returncode != 0
    print(f"MUTATION {name}: {'RED' if red else 'NOT-RED!!'}")
    if not red:
        score.write_bytes(orig); sys.exit(1)

score.write_bytes(orig)
r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_score.py", "-q"],
                   cwd=cwd, capture_output=True, text=True, timeout=300)
tail = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else f"exit={r.returncode}"
print("RESTORED:", tail)
same = hashlib.sha256(score.read_bytes()).hexdigest() == orig_hash
print("BYTE-IDENTICAL RESTORE:", same)
sys.exit(0 if (same and r.returncode == 0) else 1)
