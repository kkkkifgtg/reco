import math
from collections import Counter
def grams_of(t):
    out = []
    for n in (2, 3):
        for i in range(len(t) - n + 1):
            out.append(t[i:i+n])
    return out
# S10 corrected: single-doc corpus, kept={ab,bab}, norm over kept only
d = Counter(grams_of("abababab"))
kept = {"ab", "bab"}
raw = {g: 1 + math.log(c) for g, c in d.items() if g in kept}
norm = math.sqrt(sum(v*v for v in raw.values()))
print("S10 raw:", {g: repr(v) for g, v in sorted(raw.items())})
print("S10 norm:", repr(norm))
print("S10 w:", {g: repr(v/norm) for g, v in sorted(raw.items())})
# S9 d3 corrected: ab kept (df=3)
docs = ["abab", "baba", "abc"]
df = Counter()
for t in docs:
    for g in set(grams_of(t)): df[g] += 1
kept9 = {g for g, v in df.items() if v >= 2}
idf = {g: math.log((1+3)/(1+df[g])) + 1 for g in kept9}
print("S9 kept:", sorted(kept9))
for name, t in zip(("d1","d2","d3"), docs):
    c = Counter(grams_of(t))
    raw9 = {g: ((1+math.log(cnt)) if cnt else 0) * idf[g] for g, cnt in c.items() if g in kept9}
    n9 = math.sqrt(sum(v*v for v in raw9.values()))
    print(name, "norm:", repr(n9), "w:", {g: repr(v/n9) for g, v in sorted(raw9.items())})
