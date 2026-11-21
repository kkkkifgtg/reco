import json, subprocess, sys

GH = r"D:/Program Files/GitHub.cli/gh.exe"

payload = {
    "name": "reco-master-protection",
    "target": "branch",
    "enforcement": "active",
    "conditions": {
        "ref_name": {
            "include": ["~DEFAULT_BRANCH"],
            "exclude": []
        }
    },
    "bypass_actors": [],
    "rules": [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "creation"},
        {
            "type": "pull_request",
            "parameters": {
                "required_approving_review_count": 1,
                "dismiss_stale_reviews_on_push": True,
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_review_thread_resolution": True,
                "allowed_merge_methods": ["merge", "squash"]
            }
        }
    ]
}

r = subprocess.run([GH, "api", "-X", "POST", "repos/kkkkifgtg/reco/rulesets",
                    "-H", "Accept: application/vnd.github+json",
                    "--input", "-"],
                   input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8")
print("status:", r.returncode)
print((r.stdout or r.stderr)[:600])
