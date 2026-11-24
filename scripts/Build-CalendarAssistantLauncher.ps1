$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$outputDir = Join-Path $projectRoot 'dist\desktop'
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
$scriptPath = Join-Path $PSScriptRoot 'Start-CalendarAssistant.ps1'
$source = (Get-Content (Join-Path $PSScriptRoot 'CalendarAssistantLauncher.cs') -Raw).
    Replace('__SCRIPT_PATH__', $scriptPath.Replace('"', '""'))
$generatedSource = Join-Path $outputDir 'CalendarAssistantLauncher.cs'
Set-Content -LiteralPath $generatedSource -Value $source -Encoding UTF8
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
& $compiler /nologo /target:exe /optimize+ "/out:$outputDir\Calendar Assistant.exe" $generatedSource
if ($LASTEXITCODE -ne 0) { throw 'Launcher compilation failed.' }
# Give the desktop copy the current checkout path, even after moving the repo.
$script = (Get-Content -LiteralPath $scriptPath -Raw).Replace(
    '[string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)',
    "[string]`$ProjectRoot = '$($projectRoot.Replace("'", "''"))'"
)
Set-Content -LiteralPath (Join-Path $outputDir 'Start-CalendarAssistant.ps1') -Value $script -Encoding UTF8
Write-Host "Desktop launchers prepared in $outputDir"
