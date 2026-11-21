import os, sys
bat = (
    "@echo off\r\n"
    "rem reco GUI launcher (W18b): pythonw = no console window\r\n"
    "rem RECO_PERF/RECO_PERF_TABS need stdout: python.exe branch below\r\n"
    "setlocal\r\n"
    "set RECO_ROOT=%~dp0..\r\n"
    "set QT_QPA_PLATFORM=\r\n"
    "if defined RECO_PERF goto :console\r\n"
    "if defined RECO_PERF_TABS goto :console\r\n"
    "start \"\" \"%RECO_ROOT%\\.venv\\Scripts\\pythonw.exe\" -m reco_ui %*\r\n"
    "endlocal\r\n"
    "exit /b 0\r\n"
    ":console\r\n"
    "\"%RECO_ROOT%\\.venv\\Scripts\\python.exe\" -m reco_ui %*\r\n"
)
with open("scripts/reco-ui.bat", "wb") as f:
    f.write(bat.encode("ascii"))
print("bat bytes:", os.path.getsize("scripts/reco-ui.bat"), "ascii-crlf")
sys.path.insert(0, os.path.join(os.getcwd(), "packages", "reco-ui", "src"))
sys.path.insert(0, os.path.join(os.getcwd(), "packages", "reco-io", "src"))
sys.path.insert(0, os.path.join(os.getcwd(), "packages", "reco-store", "src"))
sys.path.insert(0, os.path.join(os.getcwd(), "packages", "reco-core", "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
from reco_ui.app import MainWindow, PAGE_ORDER
win = MainWindow(store=None)
win._on_nav(0)
after = win._stack.currentIndex()
assert after == 0, "nav switch broken: %d" % after   # PAGE_ORDER[0]=liked
win._on_nav(1)
assert win._stack.currentIndex() == 1, "candidate switch broken"
print("NAV FIX OK: liked=%d candidate=%d" % (win._stack.currentIndex(), win._stack.currentIndex()))
