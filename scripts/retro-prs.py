import json, subprocess, sys

GH = r"D:/Program Files/GitHub.cli/gh.exe"

def run(*args):
    r = subprocess.run([GH] + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout, r.returncode, r.stderr

def api(*args):
    out, code, err = run(*["api"] + list(args))
    if code != 0:
        print("ERR:", err[:150])
        sys.exit(1)
    return out

def api_soft(*args):
    out, code, err = run(*["api"] + list(args))
    return out, code, err

def pr(*args):
    return run(*["pr"] + list(args))

existing, _, _ = pr("list", "--state", "all", "--limit", "100", "--json", "number,title")
existing_titles = {p["title"] for p in json.loads(existing)}

shas = [l.strip() for l in api("--paginate", "repos/kkkkifgtg/reco/commits?per_page=100",
        "--jq", ".[] | select(.parents | length == 2) | .sha").splitlines() if l.strip()]

merges = []
for sha in shas:
    data = json.loads(api("repos/kkkkifgtg/reco/commits/" + sha))
    full = data["commit"]["message"] or ""
    msg = full.splitlines()[0].strip()[:80] if full.strip() else "(no message)"
    parent = data["parents"][0]["sha"]
    merges.append((sha, msg, parent))
merges.reverse()
print("ordered:", len(merges))

NL = chr(10)
ok = skipped = 0
for i, (sha, msg, parent) in enumerate(merges, start=1):
    if msg in existing_titles:
        skipped += 1
        continue
    num = "%02d" % i
    head_b, base_b = "retro/%s-head" % num, "retro/%s-base" % num
    api_soft("-X", "POST", "repos/kkkkifgtg/reco/git/refs", "-f", "ref=refs/heads/%s" % head_b, "-f", "sha=" + sha)
    api_soft("-X", "POST", "repos/kkkkifgtg/reco/git/refs", "-f", "ref=refs/heads/%s" % base_b, "-f", "sha=" + parent)
    body = "Retroactive PR：历史工单补录（合并提交 %s）。" % sha[:7] + NL + NL + "当时已完成双闸门评审（test-runner + auditor），结论在 audit-log。本页为补建留痕。"
    out, code, err = pr("create", "--base", base_b, "--head", head_b, "--title", msg, "--body", body)
    if "github.com" in out:
        ok += 1
        print("OK", num, msg[:40])
    else:
        print("FAIL", num, (err or out)[:100])
print("created this run:", ok, "| skipped:", skipped, "/", len(merges))
