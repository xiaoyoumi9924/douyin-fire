# 🔥 抖音自动续火花

> 定时自动向抖音好友发送消息，保持火花不断。

![续火花应用界面](docs/images/app-overview.png)

## 立即下载

| 系统 | 下载 |
| --- | --- |
| Windows 11| [下载 Windows 版](https://github.com/xiaoyoumi9924/douyin-fire/releases/latest) |
| macOS | [下载 macOS 版](https://github.com/xiaoyoumi9924/douyin-fire/releases/latest) |
| Ubuntu| [下载 Linux 版](https://github.com/xiaoyoumi9924/douyin-fire/releases/latest) |



## 使用步骤

1. **登录**：安装并打开应用，在「登录与记录」扫码登录抖音，也可以使用 Cookie 登录。
2. **加好友**：在「好友管理」点「从聊天列表添加」，勾选要续火花的好友后添加；也可以点「添加好友」手动填写抖音聊天列表中的备注或昵称。同名好友请先在抖音设置不同备注。
3. **写消息**：在「消息管理」填写每天发送的文字或添加图片，点「保存消息」；需要为某位好友单独设置内容时，可在「好友管理」点该好友的「消息规则」。
4. **设时间**：在「自动任务」选择每日运行时间，点「保存设置」。
5. **试运行**：回「今日概览」点「试运行检查」，检查通过后点「立即发送」，在确认窗口核对好友与消息后发送。
6. **看结果**：之后每天按已保存的时间自动执行；在「今日概览」查看当天进度，或在「登录与记录」展开「运行历史」查看结果。

发送结果为「待核对」时，在「好友管理」点对应好友行的「核对结果」，先查看抖音聊天记录，选择「已发送」保存判断，或点击「未发送，补发」立即补发当前这一条。详见[处理说明](docs/manual-review.md)。

好友行里的「消息规则」可设置日常消息、暂停续火和节日祝福。支持专属文字、图片、随机/轮换/依次发送；默认开启未来一年内置的中国节日祝福，节日当天只发对应祝福，不发送日常内容。生日祝福已取消；「登录与记录」里的「检查登录」会更新登录状态。今天已尝试发送的好友，文案调整从明天生效，避免当天重复。详见[使用说明](docs/friend-rules-and-health.md)。



## 本地部署web（可选）

装完会输出本机面板地址（默认 `http://127.0.0.1:8765/`），面板会随用户登录自动启动，关掉终端不影响。
首次仍需在面板里扫码登录、加好友、写消息，最后在「自动任务」点一次「保存时间」。

**macOS / Linux（自动识别系统）**

macOS 支持 Apple Silicon；Linux 支持 x86_64，需要 systemd 用户服务和图形桌面。

```bash
curl -fsSL https://github.com/xiaoyoumi9924/douyin-fire/releases/latest/download/install.sh | bash
```

**Windows（10 / 11，在 PowerShell 中运行）**

```powershell
irm https://github.com/xiaoyoumi9924/douyin-fire/releases/latest/download/install.ps1 | iex
```

安装完成后，重新打开终端，输入下面的命令即可用默认浏览器打开面板（自定义端口也会自动记住）：

```text
fire
```

已安装过的用户重新运行对应的安装命令，即可添加 `fire` 命令。

## 来源与许可

部分实现源自 [unmev/douyin-auto-fire](https://github.com/unmev/douyin-auto-fire)。本仓库沿用 [PolyForm Noncommercial License 1.0.0](LICENSE)，仅限非商业用途；再发布需保留原许可与版权声明。自动化依赖抖音网页界面，网页改版或登录状态失效时可能需要重新适配。请只向自己有权联系的对象发送消息，并遵守抖音平台规则。
