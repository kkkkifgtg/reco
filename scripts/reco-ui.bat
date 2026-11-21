@echo off
rem reco GUI 启动器（W18 U1）：默认 pythonw.exe 不弹控制台窗口；
rem RECO_PERF/RECO_PERF_TABS 打点走 stdout，此时退回 python.exe。
setlocal
set RECO_ROOT=%~dp0..
set QT_QPA_PLATFORM=
if defined RECO_PERF goto :console
if defined RECO_PERF_TABS goto :console
"%RECO_ROOT%\.venv\Scripts\pythonw.exe" -m reco_ui %*
endlocal
exit /b 0

:console
"%RECO_ROOT%\.venv\Scripts\python.exe" -m reco_ui %*
endlocal
exit /b 0
