"""CI 助手：生成 RECO_PERF_TABS 快照（工单 13⑤）。

用法：python tests/make_tabs_snapshot.py <out.json>
独立临时库播种 → run_tabs 遍历 → 快照落盘；确定性种子保证可复现。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    out = Path(sys.argv[1])
    from reco_store import open_store
    from reco_ui.app import MainWindow, run_tabs
    from reco_ui.glue import seed_books

    with tempfile.TemporaryDirectory() as td:
        s = open_store(Path(td) / "snap.db")
        try:
            seed_books(s, books=12, liked=5, candidate=7, now="2026-11-21T00:00:00+00:00")
            code = run_tabs(MainWindow(store=s), snapshot_path=str(out))
        finally:
            s.close()
    assert code == 0
    print(f"snapshot written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
