"""工单 04 性能冒烟：200 篇合成短文提取 + 语料级向量化耗时（RECO_PERF 口径）。"""
import sys, time, tempfile
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "packages/reco-io/src")
sys.path.insert(0, "packages/reco-core/src")
from reco_io.features import extract_features
from reco_core.vectorize import char_ngram_bucket_tfidf

para = "他推门进来。\n\n“你来了。”她说。\n“嗯。”他应了一声。\n\n窗外起风了。\n\n——走吧，天不早了。\n"
body = para * 6  # ~1.6KB/篇

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    paths = []
    for i in range(200):
        p = root / f"book_{i:03d}.txt"
        p.write_text(body + f"编号{i}\n", encoding="utf-8")
        paths.append(p)
    t0 = time.perf_counter()
    texts = []
    for p in paths:
        r = extract_features(p)
        assert r.quality_flag == "ok" and r.stats["char_count"] > 200  # 每篇 ~230 非空白字符
        texts.append(body + f"编号{p.stem}\n")
    t1 = time.perf_counter()
    res = char_ngram_bucket_tfidf(texts, min_df=2)
    t2 = time.perf_counter()
extract_ms = (t1 - t0) * 1000
vec_ms = (t2 - t1) * 1000
print(f"extract: 200 files in {extract_ms:.1f} ms ({extract_ms/200:.2f} ms/file)")
print(f"vectorize: 200 docs dim={res.dim} in {vec_ms:.1f} ms")
assert extract_ms / 200 < 50, "单篇提取超预算"
assert vec_ms < 5000, "语料向量化超预算"
print("PERF-SMOKE-OK")
