from pathlib import Path

p = Path("packages/reco-ui/src/reco_ui/app.py")
src = p.read_text(encoding="utf-8")

old = 'def run(argv=None) -> int:\n    """事件循环入口；集中 QSS 在此挂载（美观不阻塞首帧骨架）。"""\n    app = QApplication.instance() or QApplication(argv or [])\n    apply_dark_theme(app)\n    win = MainWindow()\n'

new = (
    'def run(argv=None) -> int:\n'
    '    """事件循环入口；集中 QSS 在此挂载（美观不阻塞首帧骨架）。\n\n'
    '    W18b：生产入口必须挂真实 Store（默认 db=项目根 reco.db）——\n'
    '    store=None 时 _start_load 早退，专区页将永远停在「待加载」（用户实测 bug）。\n'
    '    """\n'
    '    app = QApplication.instance() or QApplication(argv or [])\n'
    '    apply_dark_theme(app)\n'
    '    _default_db = Path(__file__).resolve().parents[3] / "reco.db"\n'
    '    from reco_store import open_store as _open_store\n\n'
    '    _store = _open_store(_default_db)\n'
    '    win = MainWindow(store=_store)\n'
)

assert old in src, "anchor not found"
assert src.count(old) == 1
p.write_text(src.replace(old, new), encoding="utf-8")
print("run() fixed")
