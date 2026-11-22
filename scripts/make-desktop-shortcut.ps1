$ErrorActionPreference = 'Stop'
$targetBat = 'F:\date\adult-reco\scripts\reco-ui.bat'
$targetIcon = 'F:\date\adult-reco\packages\reco-ui\src\reco_ui\assets\reco.ico'
if (-not (Test-Path $targetBat)) { throw "missing bat: $targetBat" }
if (-not (Test-Path $targetIcon)) { throw "missing icon: $targetIcon" }
$shell = New-Object -ComObject WScript.Shell
$desktopPath = [Environment]::GetFolderPath('Desktop')
Get-ChildItem $desktopPath -Filter '*.lnk' | Where-Object { $_.BaseName -like '*reco*' } | Remove-Item -Force
$linkPath = Join-Path $desktopPath 'reco.lnk'
$shortcut = $shell.CreateShortcut($linkPath)
$shortcut.TargetPath = $targetBat
$shortcut.WorkingDirectory = 'F:\date\adult-reco\scripts'
$shortcut.IconLocation = $targetIcon + ',0'
$shortcut.Description = 'reco - local novel recommender'
$shortcut.WindowStyle = 1
$shortcut.Save()
$verify = $shell.CreateShortcut($linkPath)
Write-Output ('Exists  : ' + (Test-Path $linkPath))
Write-Output ('Target  : ' + $verify.TargetPath)
Write-Output ('Icon    : ' + $verify.IconLocation)
Write-Output ('WorkDir : ' + $verify.WorkingDirectory)
Write-Output ('Desktop : ' + $linkPath)
