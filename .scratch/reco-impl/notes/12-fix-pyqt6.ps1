$p = 'F:\date\adult-reco\reco\.venv\Lib\site-packages\PyQt6'
if (Test-Path $p) {
  $dll = Join-Path $p 'Qt6\bin\concrt140.dll'
  if (Test-Path $dll) {
    Write-Output ("ATTR=" + (Get-Item -LiteralPath $dll -Force).Attributes)
  }
  try {
    Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction Stop
    Write-Output 'DELETE_OK'
  } catch {
    Write-Output ("DELETE_FAIL: " + $_.Exception.Message)
  }
} else {
  Write-Output 'ALREADY_GONE'
}
