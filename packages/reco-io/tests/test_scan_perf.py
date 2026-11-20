"""工单 02——RECO_PERF 打点与千文件性能门禁（<1s）。"""
from __future__ import annotations

import re
import time

import reco_io.perf as perf
from reco_io import scan_zones


def _make_corpus(tmp_path, dirs: int, per_dir: int) -> int:
    total = 0
    for d in range(dirs):
        zone_dir = tmp_path / "candidate" / f"dir_{d:02d}"
        zone_dir.mkdir(parents=True)
        for i in range(per_dir):
            (zone_dir / f"book_{d:02d}_{i:03d}.txt").write_bytes(b"synthetic text\n" * 16)
            total += 1
    return total


def test_thousand_files_scan_under_one_second(tmp_path):
    assert _make_corpus(tmp_path, dirs=25, per_dir=40) == 1000
    start = time.perf_counter()
    result = scan_zones(tmp_path, zones=("candidate",))
    elapsed = time.perf_counter() - start
    assert len(result.fingerprints) == 1000
    assert elapsed < 1.0, f"千文件扫描耗时 {elapsed:.3f}s，超过 1s 门禁"


def test_perf_span_off_is_silent(capsys):
    perf.perf_enabled = False
    with perf.perf_span("scan.scan_zones"):
        pass
    assert capsys.readouterr().err == ""


def test_perf_span_format_phase_ms(capsys):
    perf.perf_enabled = True
    try:
        with perf.perf_span("scan.scan_zones"):
            sum(range(1000))
    finally:
        perf.perf_enabled = False
    err = capsys.readouterr().err
    assert re.fullmatch(r"phase=scan\.scan_zones ms=\d+\.\d{3}\n", err), err


def test_scan_emits_perf_line_within_budget(tmp_path, capsys):
    assert _make_corpus(tmp_path, dirs=4, per_dir=25) == 100
    perf.perf_enabled = True
    try:
        result = scan_zones(tmp_path, zones=("candidate",))
    finally:
        perf.perf_enabled = False
    err = capsys.readouterr().err
    match = re.search(r"phase=scan\.scan_zones ms=(\d+\.\d{3})", err)
    assert match, err
    assert float(match.group(1)) < 1000.0
    assert len(result.fingerprints) == 100
