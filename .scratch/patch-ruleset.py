import json, subprocess, sys, tempfile, os

GH = r"D:/Program Files/GitHub.cli/gh.exe"

def gh_api(args, input_data=None):
    if isinstance(args, str):
        args = [args]
    cmd = [GH, "api"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", input=input_data)
    return r.returncode, r.stdout, r.stderr

code, out, err = gh_api("repos/kkkkifgtg/reco/rulesets/27782776")
rs = json.loads(out)
rs["bypass_actors"] = [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
for k in ("id", "source_type", "source", "created_at", "updated_at", "ruleset_id"):
    rs.pop(k, None)
tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
json.dump(rs, tmp, ensure_ascii=False)
tmp.close()
code, out, err = gh_api(["-X", "PUT", "repos/kkkkifgtg/reco/rulesets/27782776", "--input", tmp.name])
os.unlink(tmp.name)
print("PUT status:", code, (out or err)[:200])
