"""工单 04——char n-gram 哈希桶（红测试，S8）。

桶号 = blake2b(ngram, 8B) 大端 mod num_buckets，跨运行稳定；
输出只含 (slot, count) 数值对，绝不含 n-gram 字符串（§5.6/B2）。
冻结字面值来源：.scratch/reco-impl/notes/04-freeze-literals.py（stdlib 独立计算）。
"""
from __future__ import annotations

from reco_io.features import char_ngram_buckets

# blake2b(digest_size=8, big) % 200000 的冻结槽位（实现期 stdlib 复算后钉死）
S8_FROZEN = {
    "好好": 170791, "好学": 142099, "学习": 45598, "习天": 51166,
    "天天": 55095, "天向": 113315, "向上": 192554,
    "好好学": 115630, "好学习": 17310, "学习天": 33012,
    "习天天": 88885, "天天向": 57904, "天向上": 61311,
}


def test_S8_real_hash_stable_and_frozen():
    b1 = char_ngram_buckets("好好学习天天向上")
    b2 = char_ngram_buckets("好好学习天天向上")
    assert b1 == b2  # 同输入必同桶（跨运行稳定）
    assert len(b1) == 13
    assert set(s for s, _ in b1) == set(S8_FROZEN.values())  # 13 个冻结槽位全中（无碰撞）
    assert all(c == 1 for _, c in b1)


def test_S8_injective_stub_distinct_buckets():
    counter = iter(range(1000))
    b = char_ngram_buckets("好好学习天天向上", hash_fn=lambda g: next(counter))
    assert len(b) == 13
    assert sum(c for _, c in b) == 13
    assert all(c == 1 for _, c in b)


def test_S8_collision_stub_merges_counts():
    pinned = {"好好": 5, "好学": 5, "好学习": 7}
    seen = []

    def stub(gram):
        if gram in pinned:
            return pinned[gram]
        seen.append(gram)
        return 100 + len(seen) * 7  # 其余 gram 唯一槽，避开 5/7

    b = char_ngram_buckets("好好学习天天向上", hash_fn=stub)
    assert len(b) == 12  # 好好/好学 碰撞合并
    assert sum(c for _, c in b) == 13
    assert (5, 2) in b
    assert (7, 1) in b
    assert [s for s, _ in b] == sorted(s for s, _ in b)
    assert all(isinstance(s, int) and isinstance(c, int) for s, c in b)


def test_S8_slots_within_num_buckets():
    text = "好好学习天天向上" * 50
    b = char_ngram_buckets(text, num_buckets=1000)
    assert all(0 <= s < 1000 for s, _ in b)
    n = len(text)
    assert sum(c for _, c in b) == (n - 1) + (n - 2)  # 2-gram 399 + 3-gram 398
    assert not any(isinstance(x, str) for pair in b for x in pair)
