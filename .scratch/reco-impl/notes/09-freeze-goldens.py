def r(x): return repr(x)
def clip(d, c=0.05): return max(-c, min(c, d))
def laplace(sp, sn): return (sp + 1) / (sp + sn + 2)
print("== S5 exact test path: 10 hits w=1 + 2 dislikes w=1 (all age0), then probes ==")
disp = 0.5; sp = 0.0; sn = 0.0
trace = []
for k in range(10):
    sp += 1.0
    d = clip(laplace(sp, sn) - disp); disp += d
    trace.append(r(disp))
for k in range(2):
    sn += 1.0
    d = clip(laplace(sp, sn) - disp); disp += d
    trace.append(r(disp) + "(d" + str(k+1) + ")")
print("warm trace =", trace, "sums", (sp, sn))
base = disp
for name, w in (("newer30", 0.5 ** (30 / 90)), ("older60", 0.5 ** (60 / 90))):
    tgt = laplace(sp + w, sn)
    d = clip(tgt - base)
    print(name, "target", r(tgt), "delta", r(d), "final", r(base + d))
print("== S7 derived confidence probes ==")
def conf(np_, nn_, axes_total, axes_covered):
    cap = 0.3 if np_ < 10 else 1.0
    sf = min(1.0, (np_ + nn_) / 40)
    cov = (axes_covered / axes_total) if axes_total else 0.0
    cons = (1 - abs(np_ - nn_) / (np_ + nn_)) if (np_ + nn_) else 0.0
    return cap * (0.4 * sf + 0.35 * cov + 0.25 * cons), cap
for name, args in (("empty", (0,0,0,0)), ("f1_3pos", (3,0,3,1)), ("f2_20_5", (20,5,1,1)),
                   ("f3_20_100", (20,100,1,1)), ("f4_9_100", (9,100,1,1)),
                   ("f5_9_9", (9,9,1,1)), ("f6_10_9", (10,9,1,1)), ("f7_1000", (1000,1000,1,1))):
    v, c = conf(*args)
    print(name, "=", r(v), "cap", r(c))
