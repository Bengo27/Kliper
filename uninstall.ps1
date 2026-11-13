# Removes Clip Genie (engine, panel, settings and downloaded models)
$Base   = Join-Path $env:LOCALAPPDATA "GenieStudioFX\ClipGenie"
$ExtDir = Join-Path $env:APPDATA "Adobe\CEP\extensions\com.geniestudiofx.clipgenie"
$Data   = Join-Path $env:APPDATA "GenieStudioFX\ClipGenie"

Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*ClipGenie*run_engine*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

foreach ($l in @((Join-Path ([Environment]::GetFolderPath("Desktop")) "Clip Genie.lnk"), (Join-Path ([Environment]::GetFolderPath("Programs")) "Clip Genie.lnk"))) { if (Test-Path $l) { Remove-Item -Force $l } }
foreach ($d in @($ExtDir, $Base, $Data)) {
    if (Test-Path $d) { Remove-Item -Recurse -Force $d; Write-Host "Removed $d" }
}
Write-Host "Clip Genie uninstalled. Your exported TikToks were not touched." -ForegroundColor Green
