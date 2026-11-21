import os, sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.getcwd(), "packages", "reco-io", "src"))
sys.path.insert(0, os.path.join(os.getcwd(), "packages", "reco-store", "src"))
from datetime import datetime, timezone
from reco_io.scanner import scan_zones
from reco_store import open_store, dedup_key, normalize_book_name

def to_int64(v: int) -> int:
    """Windows st_dev/st_ino 是无符号 64 位；SQLite INTEGER 是有符号 64 位。
    双字节补码回绕（与 os.stat 的 C 层一致），幂等可逆。"""
    return (v & 0xFFFFFFFFFFFFFFFF) - (1 << 64) if (v & 0xFFFFFFFFFFFFFFFF) >= (1 << 63) else (v & 0xFFFFFFFFFFFFFFFF)

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
        "st_dev": to_int64(int(fp.st_dev)), "st_ino": to_int64(int(fp.st_ino)),
        "display_name": name,
        "normalized_name": normalize_book_name(name),
        "dedup_key": dedup_key(int(fp.size_bytes), name),
    }
    store.append_event("file_added", payload=payload, occurred_at=now,
                       config_fingerprint="captain/import/1", actor="system")
    added += 1
store.close()
print("added:", added, "| ignored:", list(result.ignored))
