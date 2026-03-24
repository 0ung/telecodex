param(
    [string]$OutputDir = ".\dist",
    [string]$BinaryName = "telecodex.exe"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$outputDirPath = Join-Path $root $OutputDir
$binaryPath = Join-Path $outputDirPath $BinaryName

if (Test-Path $outputDirPath) {
    Get-ChildItem -Path $outputDirPath -Force | Remove-Item -Recurse -Force
} else {
    New-Item -ItemType Directory -Force -Path $outputDirPath | Out-Null
}

$env:CGO_ENABLED = "0"

go build -trimpath -ldflags="-s -w -H=windowsgui" -o $binaryPath (Join-Path $root "cmd\app")

Copy-Item (Join-Path $root "README.md") -Destination (Join-Path $outputDirPath "README.md") -Force
Copy-Item (Join-Path $root "LICENSE") -Destination (Join-Path $outputDirPath "LICENSE") -Force

$configTemplate = @'
bot_token: "1234567890:AAEXAMPLE_REPLACE_ME"
allowed_user_id: 123456789
language: "ko"
model: ""
codex_path: "C:/Users/your-name/AppData/Roaming/npm/codex.cmd"
workspace_dir: "C:/Users/your-name/work/my-project"
poll_timeout_sec: 30
progress_update_sec: 2
log_file: "./app.log"
'@

Set-Content -Path (Join-Path $outputDirPath "config.yaml") -Value $configTemplate -NoNewline

Write-Host "Built $binaryPath"
