$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
# 移除旧的中文文件名快捷方式（控制台显示乱码的源头）
Get-ChildItem $desktop -Filter '*.lnk' | Where-Object { $_.BaseName -like '*reco*' } | Remove-Item -Force
$lnk = $ws.CreateShortcut((Join-Path $desktop 'reco.lnk'))
$lnk.TargetPath = 'F:\date\adult-reco\reco\scripts\reco-ui.bat'
$lnk.WorkingDirectory = 'F:\date\adult-reco\reco\scripts'
$lnk.IconLocation = 'F:\date\adult-reco\reco\packages\reco-ui\src\reco_ui\assets\reco.ico,0'
$lnk.Description = 'reco - local novel recommender'
$lnk.Save()
Write-Output ('Exists: ' + (Test-Path (Join-Path $desktop 'reco.lnk')))
