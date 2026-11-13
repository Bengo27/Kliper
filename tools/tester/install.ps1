# Installs the Clip Genie tester copy for this Windows user (no admin needed).
# Copies the "ClipGenie" folder next to this script to %LOCALAPPDATA%\GenieStudioFX\ClipGenie,
# makes Desktop + Start menu shortcuts and starts Clip Genie.
param([string]$Target = (Join-Path $env:LOCALAPPDATA "GenieStudioFX\ClipGenie"), [switch]$Test)   # -Test: no stopping, no shortcuts, no start (for checking the copy)
$ErrorActionPreference = "Stop"
$Src  = Join-Path $PSScriptRoot "ClipGenie"
$Data = Join-Path $env:APPDATA "GenieStudioFX\ClipGenie"

Write-Host ""
Write-Host "  Installing Clip Genie (tester copy)..." -ForegroundColor Cyan
if (-not (Test-Path (Join-Path $Src "engine\run_app.pyw"))) {
    Write-Host "  Can't find the ClipGenie folder next to this file. Unzip the whole download first, then run it again." -ForegroundColor Red
    Read-Host "  Press Enter to close"; exit 1
}

# Stop a running Clip Genie so its files can be replaced
if (-not $Test) { Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*ClipGenie*run_*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }

New-Item -ItemType Directory -Force $Target | Out-Null
Write-Host "  Copying files (about 3 GB, takes a minute or two)..."
foreach ($d in @("app", "engine", "python", "ffmpeg")) {
    robocopy (Join-Path $Src $d) (Join-Path $Target $d) /MIR /NFL /NDL /NJH /NJS /NP /R:2 /W:1 | Out-Null
    if ($LASTEXITCODE -ge 8) { Write-Host "  Copy failed ($d)." -ForegroundColor Red; Read-Host "  Press Enter to close"; exit 1 }
}
foreach ($f in @("README.md", "uninstall.ps1")) { Copy-Item -Force (Join-Path $Src $f) $Target }
Get-ChildItem -Recurse -File $Target -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

# First run: empty name tag (the welcome screen asks for the Twitch name)
New-Item -ItemType Directory -Force $Data | Out-Null
$Settings = Join-Path $Data "settings.json"
if (-not (Test-Path $Settings)) {
    [IO.File]::WriteAllText($Settings, '{ "style": { "watermark": "" } }', (New-Object Text.UTF8Encoding $false))   # no BOM: json.load can't read one
}

# Shortcuts
$Shell = New-Object -ComObject WScript.Shell
$Links = @((Join-Path ([Environment]::GetFolderPath("Desktop")) "Clip Genie.lnk"),
           (Join-Path ([Environment]::GetFolderPath("Programs")) "Clip Genie.lnk"))
if ($Test) { $Links = @() }
foreach ($l in $Links) {
    $s = $Shell.CreateShortcut($l)
    $s.TargetPath = Join-Path $Target "python\pythonw.exe"
    $s.Arguments = '"' + (Join-Path $Target "engine\run_app.pyw") + '"'
    $s.WorkingDirectory = Join-Path $Target "engine"
    $s.IconLocation = (Join-Path $Target "app\icon.ico") + ",0"
    $s.Description = "Clip Genie: Twitch streams to TikToks"
    $s.Save()
}

Write-Host ""
Write-Host "  Done! Clip Genie is on your Desktop." -ForegroundColor Green
Write-Host "  The first stream takes longer: it downloads the speech model once (about 0.5 GB)."
if (-not $Test) { Start-Process -FilePath (Join-Path $Target "python\pythonw.exe") -ArgumentList ('"' + (Join-Path $Target "engine\run_app.pyw") + '"') -WorkingDirectory (Join-Path $Target "engine") }
if (-not $Test) { Start-Sleep -Seconds 4 }
