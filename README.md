# Douyin Fire

抖音自动续火花桌面应用。扫码登录后，每天在设定的时间自动给指定好友发消息；全部在本机运行，不需要服务器、VPS 或开放公网端口，也无需预装 Python 和浏览器。

## 立即下载

| 系统 | 下载 |
| --- | --- |
| Windows 11 及以上（x64） | [下载 Windows 版](https://github.com/xiaoyoumi9924/douyin-fire/releases/latest) |
| macOS 14 及以上（Apple 芯片） | [下载 macOS 版](https://github.com/xiaoyoumi9924/douyin-fire/releases/latest) |
| Debian 12+ / Ubuntu 22.04+（x64） | [下载 Linux 版](https://github.com/xiaoyoumi9924/douyin-fire/releases/latest) |

下载页会列出当前版本的全部安装包，按系统选择 `.exe`、`.dmg` 或 `.AppImage`。安装包未签名：Windows 可能提示“未知发布者”，macOS 首次打开需在「系统设置 → 隐私与安全性」中放行；Linux 需要发行版提供 Chromium 图形运行库（[系统要求](https://playwright.dev/python/docs/intro#system-requirements)）。

## 使用步骤

1. **登录**：安装并打开应用，用抖音扫码登录。
2. **加好友**：在「好友管理」添加要续火花的好友，双击昵称可改名。
3. **写消息**：在「消息管理」填写每天发送的文字或图片，保存。
4. **设时间**：在「自动任务」选择每日运行时间，点「保存时间」。
5. **试运行**：回「首页」点「立即发送」，在确认窗口核对好友与消息后发送。
6. **看结果**：之后每天自动执行，结果在「登录与记录」的运行历史里。

> 定时由系统计划任务（macOS launchd、Windows 任务计划程序、Linux cron）触发，**不需要让应用一直开着**；到点需要电脑开机、已登录当前用户且网络可用。如果那时关机或睡眠错过了，当天开机或唤醒后会自动补跑一次（最多尝试 3 次，已经发过的好友不会重复发送）。

## 常见问题

- **到点没有执行？** 确认点过「保存时间」（只选时间不算）、电脑没有关机或休眠；错过的话开机/唤醒后会自动补跑，仍然没执行再看登录状态是否失效。
- **提示登录失效？** 在「登录与记录」里重新扫码即可。
- **换电脑或重装？** 把应用数据目录复制到新机器的同一位置：macOS `~/Library/Application Support/Douyin Fire`，Windows `%LOCALAPPDATA%\Douyin Fire`，Linux `~/.local/share/douyin-fire`（也可用 `DOUYIN_DATA_DIR` 指定）。

## 数据与隐私

账号、Cookie / Storage State、消息图片和运行记录只保存在本机上述目录，不会上传。Storage State 等同于登录凭证，请按密码保管，也不要提交到公开仓库。

## 开发

本地运行与打包步骤见 [CONTRIBUTING.md](.github/CONTRIBUTING.md)。推送 `v*` 标签会由 GitHub Actions 构建三个平台的安装包并创建 Release。

## 来源与许可

部分实现源自 [unmev/douyin-auto-fire](https://github.com/unmev/douyin-auto-fire)。本仓库沿用 [PolyForm Noncommercial License 1.0.0](LICENSE)，仅限非商业用途；再发布需保留原许可与版权声明。自动化依赖抖音网页界面，网页改版或登录状态失效时可能需要重新适配。请只向自己有权联系的对象发送消息，并遵守抖音平台规则。
