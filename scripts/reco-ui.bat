@echo off
rem reco GUI launcher (W18b): pythonw = no console window
rem RECO_PERF/RECO_PERF_TABS need stdout: python.exe branch below
setlocal
set RECO_ROOT=%~dp0..
set QT_QPA_PLATFORM=
if defined RECO_PERF goto :console
if defined RECO_PERF_TABS goto :console
start "" "%RECO_ROOT%\.venv\Scripts\pythonw.exe" -m reco_ui %*
endlocal
exit /b 0
:console
"%RECO_ROOT%\.venv\Scripts\python.exe" -m reco_ui %*
