"""从 wheels/ 直接解压 PyQt6-Qt6 wheel 到 site-packages（绕开 pip 的 AV 文件锁）。"""
import os
import sys
import time
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[3]
wheel = sorted(root.glob("wheels/pyqt6_qt6-*.whl"))[0]
target = root / ".venv" / "Lib" / "site-packages"

with zipfile.ZipFile(wheel) as zf:
    names = zf.namelist()
    failed = []
    for name in names:
        dest = target / name
        for attempt in range(5):
            try:
                zf.extract(name, target)
                if name.endswith("/"):
                    dest.mkdir(parents=True, exist_ok=True)
                break
            except PermissionError:
                if attempt == 4:
                    failed.append(name)
                else:
                    time.sleep(1.5)
print(f"extracted={len(names) - len(failed)} failed={len(failed)}")
for f in failed[:10]:
    print("FAILED:", f)
sys.exit(1 if failed else 0)
