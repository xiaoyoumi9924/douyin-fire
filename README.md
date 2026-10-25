# 🔥 抖音自动续火花

> 定时自动向抖音好友发送消息，保持火花不断。

## 立即下载

| 系统 | 下载 |
| --- | --- |
| Windows 11| [下载 Windows 版](https://github.com/xiaoyoumi9924/douyin-fire/releases/latest) |
| macOS | [下载 macOS 版](https://github.com/xiaoyoumi9924/douyin-fire/releases/latest) |
| Ubuntu| [下载 Linux 版](https://github.com/xiaoyoumi9924/douyin-fire/releases/latest) |


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

几点通用规则：

- 请先把应用拖进「应用程序」文件夹（macOS）或安装到固定目录后再保存时间。移动应用后，重新打开应用即可自动修复计划任务；也可以在「自动任务」里再点一次「保存时间」。
- 到点需要电脑开机并登录当前用户；如果睡眠或关机错过，会在恢复后的 30 分钟内补跑一次，当天最多尝试 3 次。
- 如果某次运行有好友失败，后面的 30 分钟检查点会只补发失败的好友；已确认送达的不会重复发送。
- 如果当天已经过了设定时间才第一次保存，今天不会立刻补发，会从下一个设定时间开始。
- 当天已确认送达的好友不会重复发送。
- 系统计划任务实际由应用内置的后台程序执行；执行期间应用界面可能暂时显示为闲置，刷新页面即可看到最新结果。

## 来源与许可

部分实现源自 [unmev/douyin-auto-fire](https://github.com/unmev/douyin-auto-fire)。本仓库沿用 [PolyForm Noncommercial License 1.0.0](LICENSE)，仅限非商业用途；再发布需保留原许可与版权声明。自动化依赖抖音网页界面，网页改版或登录状态失效时可能需要重新适配。请只向自己有权联系的对象发送消息，并遵守抖音平台规则。
