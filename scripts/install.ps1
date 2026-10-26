<#
抖音自动续火花 · Windows 一键安装

  irm https://github.com/xiaoyoumi9924/douyin-fire/releases/latest/download/install.ps1 | iex

先设环境变量可以改默认行为：
  $env:DOUYIN_PORT = '8765'        面板端口（默认 8765）
  $env:DOUYIN_SCHEDULE = '07:00'   装完顺便设好每日时间
  $env:DOUYIN_NO_OPEN = '1'        装完不自动打开浏览器
  $env:DOUYIN_UNINSTALL = '1'      卸载面板服务和应用（数据保留）

下载后也可以直接调用：
  .\install.ps1 -Port 8765 -Schedule 07:00
  .\install.ps1 -Uninstall
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$Repo = 'xiaoyoumi9924/douyin-fire'
$TaskName = 'DouyinFirePanel'
$AppName = 'Douyin Fire'
# cmd / vbs 文件要用系统本地编码，中文用户名才不会乱码。
$TextEncoding = if ($PSVersionTable.PSEdition -eq 'Desktop') { 'Default' } else { 'OEM' }

if (-not ($env:OS -eq 'Windows_NT')) {
    throw '这个脚本只支持 Windows。macOS / Linux 请用另外的安装脚本。'
}

$Port = 8765
$Schedule = ''
$DoUninstall = $false
$DoOpen = $true

if ($env:DOUYIN_PORT) { $Port = [int]$env:DOUYIN_PORT }
if ($env:DOUYIN_SCHEDULE) { $Schedule = $env:DOUYIN_SCHEDULE }
if ($env:DOUYIN_NO_OPEN -eq '1') { $DoOpen = $false }
if ($env:DOUYIN_UNINSTALL -eq '1') { $DoUninstall = $true }

for ($i = 0; $i -lt $args.Count; $i++) {
    switch ($args[$i]) {
        '-Port' { $i++; $Port = [int]$args[$i] }
        '-Schedule' { $i++; $Schedule = [string]$args[$i] }
        '-NoOpen' { $DoOpen = $false }
        '-Uninstall' { $DoUninstall = $true }
        default { throw "不认识的参数：$($args[$i])" }
    }
}

$PanelUrl = "http://127.0.0.1:$Port/"

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor DarkGray
}

function Get-AppDir {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\$AppName"),
        (Join-Path $env:ProgramFiles $AppName)
    )
    if (${env:ProgramFiles(x86)}) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} $AppName)
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path (Join-Path $candidate "$AppName.exe"))) {
            return $candidate
        }
    }
    $roots = @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    foreach ($root in $roots) {
        $entries = Get-ItemProperty $root -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -like "$AppName*" }
        foreach ($entry in $entries) {
            if ($entry.InstallLocation -and (Test-Path (Join-Path $entry.InstallLocation "$AppName.exe"))) {
                return $entry.InstallLocation
            }
        }
    }
    return $null
}

function Clear-PanelPort {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return }
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -like 'douyin-fire-backend*') {
        Write-Step "停止旧面板进程 $($process.Id)"
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
        return
    }
    $name = if ($process) { $process.ProcessName } else { '未知程序' }
    throw "端口 $Port 已被 $name 占用。先关掉它，或设置 `$env:DOUYIN_PORT 换一个端口。"
}

function Wait-Panel([int]$Seconds = 90) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "${PanelUrl}api/state" -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return $true }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

function Invoke-Uninstall {
    Write-Step '停止面板服务'
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

    $dataDir = Join-Path $env:LOCALAPPDATA $AppName
    Remove-Item (Join-Path $dataDir 'panel.cmd'), (Join-Path $dataDir 'panel.vbs') -Force -ErrorAction SilentlyContinue

    $appDir = Get-AppDir
    if ($appDir) {
        Write-Step "卸载 $appDir"
        $uninstaller = Get-ChildItem -Path $appDir -Filter 'Uninstall*.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($uninstaller) {
            Start-Process -FilePath $uninstaller.FullName -ArgumentList '/S' -Wait
        } else {
            Write-Host "没找到卸载程序，请在「设置 → 应用」里卸载 $AppName" -ForegroundColor Yellow
        }
    }
    Write-Host "已移除面板服务。数据保留在 $dataDir" -ForegroundColor Yellow
}

function Install-Panel {
    Clear-PanelPort
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

    $dataDir = Join-Path $env:LOCALAPPDATA $AppName
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

    Write-Step '下载安装包'
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
        -Headers @{ 'User-Agent' = 'douyin-fire-installer' }
    $asset = $release.assets | Where-Object { $_.name -like '*.exe' } | Select-Object -First 1
    if (-not $asset) { throw "最新的 $($release.tag_name) 里没有 Windows 安装包。" }

    $tempDir = Join-Path $env:TEMP ("douyin-fire-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
    $setup = Join-Path $tempDir 'setup.exe'
    Write-Host "    $($asset.name)（约 380 MB）"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $setup -UseBasicParsing

    Write-Step '静默安装'
    $process = Start-Process -FilePath $setup -ArgumentList '/S' -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw "安装器退出码 $($process.ExitCode)" }

    $appDir = Get-AppDir
    if (-not $appDir) { throw "装完了但没找到 $AppName 的安装目录。" }

    $backend = Join-Path $appDir 'resources\backend\douyin-fire-backend\douyin-fire-backend.exe'
    $browsers = Join-Path $appDir 'resources\ms-playwright'
    if (-not (Test-Path $backend)) { throw "找不到后台程序：$backend" }

    Write-Step '注册开机自启的面板服务'
    $panelCmd = Join-Path $dataDir 'panel.cmd'
    $panelVbs = Join-Path $dataDir 'panel.vbs'
    $lines = @(
        '@echo off',
        "set `"DOUYIN_DATA_DIR=$dataDir`"",
        'set "DOUYIN_DESKTOP_MODE=1"',
        "set `"UI_PORT=$Port`"",
        "set `"PLAYWRIGHT_BROWSERS_PATH=$browsers`"",
        "`"$backend`" serve"
    )
    Set-Content -Path $panelCmd -Value $lines -Encoding $TextEncoding

    $vbs = @"
Set shell = CreateObject("WScript.Shell")
shell.Run """" & "$panelCmd" & """", 0, False
"@
    Set-Content -Path $panelVbs -Value $vbs -Encoding $TextEncoding

    $action = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\wscript.exe') -Argument "`"$panelVbs`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName

    Write-Step '等待面板启动'
    if (-not (Wait-Panel)) {
        $log = Join-Path $dataDir 'logs\panel.err.log'
        if (Test-Path $log) { Get-Content $log -Tail 20 | Write-Host }
        throw "面板没启动成功，日志在 $log"
    }

    if ($Schedule) {
        if ($Schedule -notmatch '^\d{1,2}:\d{2}$') { throw '每日时间要写成 HH:mm，例如 07:00' }
        $parts = $Schedule.Split(':')
        $body = @{ hour = [int]$parts[0]; minute = [int]$parts[1] } | ConvertTo-Json
        Write-Step "设置每日 $Schedule 自动运行"
        Invoke-RestMethod -Uri "${PanelUrl}api/schedule" -Method Post -ContentType 'application/json; charset=utf-8' -Body $body | Out-Null
    }

    Write-Host ''
    Write-Host " 安装完成" -ForegroundColor Green
    Write-Host "   面板地址   $PanelUrl"
    Write-Host "   应用位置   $appDir"
    Write-Host "   数据目录   $dataDir"
    Write-Host ''
    Write-Host '面板会在登录 Windows 时自动启动。首次使用还有四步（只需做一次）：'
    Write-Host '   1. 打开面板，用抖音扫码登录'
    Write-Host '   2. 「好友管理」加好友（要填对方备注名）'
    Write-Host '   3. 「消息管理」写每天要发的内容，保存'
    Write-Host '   4. 「自动任务」选时间并点「保存时间」——之后每天自动发'
    Write-Host ''
    Write-Host '卸载：$env:DOUYIN_UNINSTALL = ''1''; irm .../install.ps1 | iex'
    Write-Host ''

    if ($DoOpen) { Start-Process $PanelUrl }
}

try {
    if ($DoUninstall) {
        Invoke-Uninstall
    } else {
        Install-Panel
    }
} catch {
    Write-Host "错误：$($_.Exception.Message)" -ForegroundColor Red
    throw
}
