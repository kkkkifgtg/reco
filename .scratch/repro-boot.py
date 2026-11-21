import os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.argv = ["reco_ui"]
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QCoreApplication
app = QApplication(sys.argv)
sys.path.insert(0, os.path.join(os.getcwd(), "packages", "reco-ui", "src"))
from reco_ui.app import run
# can't run exec() blocking — replicate run() body exactly:
from reco_ui.app import apply_dark_theme, MainWindow
from pathlib import Path
from reco_store import open_store
apply_dark_theme(app)
_default_db = Path(__file__).resolve().parents[1] / "reco.db"
win = MainWindow(store=open_store(_default_db))
win.resize(1080, 720)
win.show()
deadline = time.time() + 8
page = win.page("liked")
while time.time() < deadline:
    QCoreApplication.processEvents()
    if page.loaded:
        break
    time.sleep(0.05)
print("liked loaded:", page.loaded, "| rows:", page.row_count(), "| status:", repr(page._status.text()))
