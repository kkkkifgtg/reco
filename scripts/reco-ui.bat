@echo off
rem reco GUI 启动器：用项目 venv 解释器启动 PyQt6 界面
setlocal
set RECO_ROOT=%~dp0..
set QT_QPA_PLATFORM=
"%RECO_ROOT%\.venv\Scripts\python.exe" -m reco_ui %*
endlocal
