$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = $ws.CreateShortcut((Join-Path $desktop 'reco 本地推荐.lnk'))
$lnk.TargetPath = 'F:\date\adult-reco\reco\scripts\reco-ui.bat'
$lnk.WorkingDirectory = 'F:\date\adult-reco\reco\scripts'
$lnk.IconLocation = 'F:\date\adult-reco\reco\packages\reco-ui\src\reco_ui\assets\reco.ico,0'
$lnk.Description = 'reco 本地小说推荐程序'
$lnk.Save()
if (Test-Path (Join-Path $desktop 'reco 本地推荐.lnk')) { Write-Output 'SHORTCUT OK' } else { Write-Output 'SHORTCUT MISSING' }
