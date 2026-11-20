$paths = @(
  'F:\date\adult-reco\reco\.venv\Lib\site-packages\PyQt6\Qt6',
  'F:\date\adult-reco\reco\.venv\Lib\site-packages\PyQt6_Qt6-6.12.0.dist-info'
)
foreach ($p in $paths) {
  if (Test-Path $p) {
    try { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction Stop; Write-Output "DEL_OK $p" }
    catch { Write-Output ("DEL_FAIL " + $_.Exception.Message) }
  } else { Write-Output "ABSENT $p" }
}
