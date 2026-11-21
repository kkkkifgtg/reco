import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.argv = ["reco_ui"]
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
from reco_ui.app import run
# run() would block on exec; instead replicate its store wiring then probe pages
from pathlib import Path
from reco_store import open_store
store = open_store(Path(__file__).resolve().parents[1] / "reco.db")
from reco_ui.app import MainWindow
win = MainWindow(store=store)
win.resize(1080, 720)
win.show()
# drive the same async path: emit page request for liked
win.page_requested.emit("liked")
# pump events until loaded (worker completes via signal)
import time
deadline = time.time() + 5
from PyQt6.QtCore import QCoreApplication
while time.time() < deadline:
    QCoreApplication.processEvents()
    if win.page("liked").loaded:
        break
    time.sleep(0.02)
page = win.page("liked")
print("liked loaded:", page.loaded, "| status:", page.summary_text() if hasattr(page, "summary_text") else page._status.text(), "| rows:", page.row_count())
assert page.loaded, "liked page still not loaded"
print("LIKED PAGE OK")
