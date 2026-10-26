# 🔥 抖音自动续火花

> 定时自动向抖音好友发送消息，保持火花不断。

## 立即下载

| 系统 | 下载 |
| --- | --- |
| Windows 11| [下载 Windows 版](https://github.com/xiaoyoumi9924/douyin-fire/releases/latest) |
| macOS | [下载 macOS 版](https://github.com/xiaoyoumi9924/douyin-fire/releases/latest) |
| Ubuntu| [下载 Linux 版](https://github.com/xiaoyoumi9924/douyin-fire/releases/latest) |

## 快速安装

装完会输出本机面板地址（默认 `http://127.0.0.1:8765/`），面板会开机自启，关掉终端不影响。
首次仍需在面板里扫码登录、加好友、写消息，最后在「自动任务」点一次「保存时间」。

**macOS（Apple Silicon）**

```bash
curl -fsSL https://github.com/xiaoyoumi9924/douyin-fire/releases/latest/download/install.sh | bash
```

**Windows（10 / 11）**

```powershell
irm https://github.com/xiaoyoumi9924/douyin-fire/releases/latest/download/install.ps1 | iex
```

**Linux（x86_64，需要 systemd）**

```bash
curl -fsSL https://github.com/xiaoyoumi9924/douyin-fire/releases/latest/download/install-linux.sh | bash
```

## 使用步骤

1. **登录**：安装并打开应用，用抖音扫码登录，也可以使用cookie登录。
2. **加好友**：在「好友管理」添加要续火花的好友，注意要使用好友的备注的名称，双击昵称可改名。
3. **写消息**：在「消息管理」填写每天发送的文字或图片，保存。
4. **设时间**：在「自动任务」选择每日运行时间，点「保存时间」。
5. **试运行**：回「首页」点「立即发送」，在确认窗口核对好友与消息后发送。
6. **看结果**：之后每天自动执行，结果在「登录与记录」的运行历史里。

## 每天定时运行（可选）

点「保存时间」后，每日任务会写入系统计划任务，**关闭应用后仍会按时运行**，不需要一直开着。

各系统的安装位置、睡眠与权限注意事项不同，请按你的系统查看对应教程：

- [Windows 自动运行教程](docs/automation-windows.md)
- [macOS 自动运行教程](docs/automation-macos.md)
- [Linux 自动运行教程](docs/automation-linux.md)

如果觉得教程比较麻烦，可以把 [源码 ZIP](https://github.com/xiaoyoumi9924/douyin-fire/archive/refs/heads/main.zip) 和对应教程一起交给 DeepSeek 或 ChatGPT，让它陪你一步步配置。

## 来源与许可

部分实现源自 [unmev/douyin-auto-fire](https://github.com/unmev/douyin-auto-fire)。本仓库沿用 [PolyForm Noncommercial License 1.0.0](LICENSE)，仅限非商业用途；再发布需保留原许可与版权声明。自动化依赖抖音网页界面，网页改版或登录状态失效时可能需要重新适配。请只向自己有权联系的对象发送消息，并遵守抖音平台规则。
