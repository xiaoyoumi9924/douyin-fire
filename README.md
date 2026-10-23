# Douyin Fire

抖音自动续火花本地桌面应用。复用当前项目里的控制面板和自动化逻辑，通过桌面窗口在本机运行；不需要 VPS、网站服务器、FRP 隧道或开放公网端口。应用仍会连接抖音网页执行你配置的操作。

## 下载

桌面安装包由 GitHub Actions 构建：

- Windows：`Douyin Fire Setup ... .exe`
- macOS Apple 芯片：`... arm64.dmg`
- macOS Intel：`... x64.dmg`
- Linux：`... .AppImage` 和 `... .deb`

目标环境：Windows 11 或更新版本；macOS 14 或更新版本；Debian 12/13 或 Ubuntu 22.04/24.04/26.04（Linux x64/arm64）。Linux 上的 Chromium 还需要发行版提供相应图形运行库，详见 [Playwright 系统要求](https://playwright.dev/python/docs/intro#system-requirements)。

手动运行 `Desktop installers` 工作流会生成可下载的构建产物。推送 `v` 开头的版本标签（例如 `v0.1.0`）会创建 GitHub Release 并附上各平台安装包。Mac 安装包目前未签名和公证，首次打开时 macOS 可能要求你在“隐私与安全性”里确认；Windows 也可能显示未签名应用提示。

## 使用

1. 安装并打开 Douyin Fire。
2. 在应用中扫码登录抖音。
3. 添加好友和消息，先运行 Dry Run 检查配置。
4. 确认无误后再手动发送或设置每日定时任务。

应用不依赖浏览器或 Python 预装环境。账号配置、Cookie/Storage State、消息图片和运行记录保存在本机的应用数据目录；不会随安装包上传。不要把这些文件或截图提交到公开仓库。

## 本地开发

需要 Python 3.12、Node.js 22 和 npm：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-desktop.txt
export PLAYWRIGHT_BROWSERS_PATH="$PWD/browser-cache"
python -m playwright install chromium
npm ci
python desktop/build_backend.py
python desktop/prepare_browsers.py
npm run desktop:dev
```

Windows PowerShell 的虚拟环境激活命令为 `.venv\Scripts\Activate.ps1`。要在本机生成安装包，先完成上面的后端与浏览器准备，再运行：

```bash
npm run desktop:package
```

GitHub Actions 会分别在 Windows、Linux、macOS Apple 芯片和 macOS Intel runner 上构建；各平台的后端和 Chromium 都必须在对应平台 runner 上打包。

## 本地数据位置

- macOS：`~/Library/Application Support/Douyin Fire`
- Windows：`%LOCALAPPDATA%\Douyin Fire`
- Linux：`$XDG_DATA_HOME/douyin-fire`，未设置时为 `~/.local/share/douyin-fire`

可以通过环境变量 `DOUYIN_DATA_DIR` 指定其他数据目录。源码开发模式直接使用项目目录；安装版的设置、凭证、图片和日志保存在以上应用数据目录。要把已有源码项目迁移到安装版，请先备份，再把 `config.json`、`.env`、`storage-state.json`、`images/`、`artifacts/` 以及多账号模式使用的 `config/accounts.json`、`config/tasks/`、`.env.account-*`、`storage_state/` 复制到该目录并保持相对目录结构。Storage State 相当于登录凭证，务必按密码保管。

## 来源与许可

本仓库以当前本地项目为基础制作桌面版，其中沿用了 [unmev/douyin-auto-fire](https://github.com/unmev/douyin-auto-fire) 的部分代码和实现。本仓库保留原始 [PolyForm Noncommercial License 1.0.0](LICENSE) 与版权声明；使用、修改和再发布都必须遵守该许可。该许可不授予商业使用权。

自动化依赖抖音网页界面，网页改版或登录状态失效时可能需要重新扫码或更新适配。请只向自己有权联系的对象发送消息，并遵守抖音平台规则。
