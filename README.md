# Douyin Fire

抖音自动续火花本地桌面应用。复用当前项目里的控制面板和自动化逻辑，通过桌面窗口在本机运行；不需要 VPS、网站服务器、FRP 隧道或开放公网端口。应用仍会连接抖音网页执行你配置的操作。

## 下载

桌面安装包由 GitHub Actions 构建：

- Windows x64：安装程序 `.exe`
- macOS Apple 芯片（arm64）：磁盘映像 `.dmg`
- Linux x64：便携应用 `.AppImage`

发布后可在 [GitHub Releases](https://github.com/xiaoyoumi9924/douyin-fire/releases/latest) 下载对应系统的安装包。目标环境：Windows 11 或更新版本；macOS 14 或更新版本（Apple 芯片）；Debian 12/13 或 Ubuntu 22.04/24.04/26.04（Linux x64）。Linux 上的 Chromium 还需要发行版提供相应图形运行库，详见 [Playwright 系统要求](https://playwright.dev/python/docs/intro#system-requirements)。

手动运行 `Desktop installers` 工作流会生成可下载的构建产物。推送 `v` 开头的版本标签（例如 `v0.1.0`）会创建 GitHub Release 并附上各平台安装包。Mac 安装包目前未签名和公证，首次打开时 macOS 可能要求你在“隐私与安全性”里确认；Windows 也可能显示未签名应用提示。

## 使用

1. 安装并打开 Douyin Fire。
2. 在应用中扫码登录抖音。
3. 添加好友和消息，先运行 Dry Run 检查配置。
4. 确认无误后再手动发送或设置每日定时任务。

应用不依赖浏览器或 Python 预装环境。账号配置、Cookie/Storage State、消息图片和运行记录保存在本机的应用数据目录；不会随安装包上传。不要把这些文件或截图提交到公开仓库。


