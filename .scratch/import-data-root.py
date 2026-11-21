import os, sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.getcwd(), "packages", "reco-io", "src"))
sys.path.insert(0, os.path.join(os.getcwd(), "packages", "reco-store", "src"))
from datetime import datetime, timezone
from reco_io.scanner import scan_zones
from reco_store import open_store, dedup_key, normalize_book_name

root = Path("F:/date/adult-reco/data")
store = open_store(Path("F:/date/adult-reco/reco/reco.db"))
now = datetime.now(timezone.utc).isoformat()
result = scan_zones(root, ("liked", "candidate", "推荐专区"))
added = 0
for fp in result.fingerprints:
    name = Path(fp.path).name
    payload = {
        "path": fp.path, "zone": fp.zone, "ext": fp.ext,
        "size_bytes": int(fp.size_bytes), "mtime_ns": int(fp.mtime_ns),
        "st_dev": int(fp.st_dev), "st_ino": int(fp.st_ino),
        "display_name": name,
        "normalized_name": normalize_book_name(name),
        "dedup_key": dedup_key(int(fp.size_bytes), name),
    }
    store.append_event("file_added", payload=payload, occurred_at=now,
                       config_fingerprint="captain/import/1", actor="system")
    added += 1
store.close()
print("added:", added, "| ignored:", list(result.ignored))
