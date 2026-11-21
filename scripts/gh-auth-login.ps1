# reco GitHub auth helper - run once in a normal PowerShell window:
#   powershell -ExecutionPolicy Bypass -File "F:\date\adult-reco\reco\scripts\gh-auth-login.ps1"
# (Pure ASCII on purpose: PowerShell 5.1 parses .ps1 as ANSI unless BOM present.)
$ErrorActionPreference = "Stop"
$gh = "D:\Program Files\GitHub.cli\gh.exe"
Write-Host "== gh auth login (web browser flow) ==" -ForegroundColor Cyan
& $gh auth login --hostname github.com --git-protocol https --web
& $gh auth status
Write-Host ""
Write-Host "Auth done. Go back to DSH and say: auth completed" -ForegroundColor Green
