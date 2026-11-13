$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '..\scripts\install.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $source), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
foreach ($name in @('Install-Fire', 'Remove-Fire')) {
    $fn = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if (-not $fn) { throw "Missing function: $name" }
    Invoke-Expression $fn.Extent.Text
}

$originalLocal = $env:LOCALAPPDATA
$originalPath = $env:Path
$originalUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$tempDir = Join-Path ([IO.Path]::GetTempPath()) ('fire-中文 test-' + [guid]::NewGuid().ToString('N'))
try {
    $env:LOCALAPPDATA = $tempDir
    $PanelUrl = 'http://127.0.0.1:9876/'
    $TextEncoding = 'OEM'
    $commandDir = Join-Path $tempDir 'Douyin Fire\bin'
    Install-Fire
    Install-Fire
    $launcher = Join-Path $commandDir 'fire.cmd'
    $content = Get-Content -Raw $launcher
    if (-not $content.Contains('start "" "http://127.0.0.1:9876/"')) { throw 'Wrong browser command or port' }
    $count = @($env:Path -split ';' | Where-Object { $_ -ieq $commandDir }).Count
    if ($count -ne 1) { throw 'Process PATH is missing or duplicated' }
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $count = @($userPath -split ';' | Where-Object { $_ -ieq $commandDir }).Count
    if ($count -ne 1) { throw 'User PATH is missing or duplicated' }
    Remove-Fire
    if (Test-Path $launcher) { throw 'Uninstall left fire.cmd behind' }
    if ($env:Path -ne $originalPath) { throw 'Uninstall changed unrelated process PATH entries' }
    if ([Environment]::GetEnvironmentVariable('Path', 'User') -ne $originalUserPath) { throw 'Uninstall changed unrelated user PATH entries' }
    Write-Host 'Windows fire lifecycle passed'
} finally {
    $env:LOCALAPPDATA = $originalLocal
    $env:Path = $originalPath
    [Environment]::SetEnvironmentVariable('Path', $originalUserPath, 'User')
    Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}
