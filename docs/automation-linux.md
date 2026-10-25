# Linux 自动运行教程

> 目标：让应用每天在固定时间自动发送，不需要一直开着窗口。
>
> 适用版本：Ubuntu 20.04+ / Debian 11+ 等主流发行版。

Linux 有两种跑法，按你的场景选一种：

| 方式 | 适合谁 | 说明 |
| --- | --- | --- |
| **A. AppImage** | 有桌面环境的 Linux 用户 | 和 Windows / macOS 一样用图形界面操作 |
| **B. 源码 + 网页面板** | 无桌面的服务器、NAS、小主机 | 只跑后端，浏览器里管理，最省内存 |

两种方式最终都靠系统 **crontab** 定时，区别只是被调起的程序不同。

---

## 方式 A：AppImage（桌面用户）

### 1. 放到固定路径

```bash
mkdir -p ~/Applications
mv ~/Downloads/Douyin.Fire-*.AppImage ~/Applications/DouyinFire.AppImage
chmod +x ~/Applications/DouyinFire.AppImage
```

**路径必须固定**：计划任务里记的是这个文件的绝对路径，移动后要重新保存时间。
不要每次从「下载」里临时运行。

### 2. 先手动跑通一次

双击（或在终端执行 `~/Applications/DouyinFire.AppImage`）打开应用，完成：

1. 扫码登录；
2. 「好友管理」确认好友备注名；
3. 「消息管理」保存今天要发的内容；
4. 首页「立即发送」核对后跑一遍。

「登录与记录」里有成功记录再继续。

### 3. 确认系统有 crontab

```bash
command -v crontab        # 应该输出 /usr/bin/crontab
sudo apt install -y cron  # 没有的话装上
```

### 4. 设置每日运行时间

「自动任务」→ 选时间 → 「保存时间」。
成功后可以用下面的命令看到写入的条目：

```bash
crontab -l | grep douyin-fire-local-schedule
```

条目形如（路径以你的实际位置为准）：

```cron
*/30 * * * * DOUYIN_DATA_DIR=/home/you/.local/share/douyin-fire ... HEADLESS=true /home/you/Applications/DouyinFire.AppImage --scheduled-run --catch-up --not-before 07:30 >> .../artifacts/scheduled.log 2>&1 # douyin-fire-local-schedule
```

含义：每 30 分钟检查一次，到点或当天错过时执行；`HEADLESS=true` 保证后台不弹浏览器窗口。

> 如果你的 cron 环境没有图形会话（常见于从 cron 直接拉起图形程序），日志里可能出现与 `DISPLAY` 相关的报错。
> 这时改用下面的「方式 B」，它不依赖图形环境。

### 5. 睡眠、关机与补跑

| 情况 | 行为 |
| --- | --- |
| 到点机器开着 | 正常执行 |
| 到点机器睡眠 / 关机 | 开机后下一个 30 分钟检查点补跑 |
| 当天已经成功跑完 | 跳过，不会重复发 |
| 当天已尝试 3 次仍失败 | 当天不再自动重试 |

当天过了设定时间才第一次保存，今天不会立刻补发，从明天开始。

### 6. 日志

| 位置 | 内容 |
| --- | --- |
| `~/.local/share/douyin-fire/artifacts/scheduled.log` | cron 每次检查的输出 |
| `~/.local/share/douyin-fire/artifacts/run.log` | 每次运行的主日志 |
| 面板「登录与记录」 | 成功 / 失败 / 待核对好友 |

---

## 方式 B：源码 + 网页面板（推荐用于服务器）

不需要桌面环境，也不需要安装 Electron，内存占用最低。

### 1. 准备系统依赖

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip cron git
sudo systemctl enable --now cron
```

### 2. 获取源码

```bash
cd ~
git clone https://github.com/xiaoyoumi9924/douyin-fire.git
cd douyin-fire
```

或者下载 [源码 ZIP](https://github.com/xiaoyoumi9924/douyin-fire/archive/refs/heads/main.zip) 后解压。

### 3. 建虚拟环境并安装依赖

> 注意：面板会固定使用项目根目录下的 `.venv/bin/python` 启动任务，所以虚拟环境**必须**建在项目根目录的 `.venv`。

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 安装 Playwright 的 Chromium 及系统依赖（需要 sudo 装 apt 包）
.venv/bin/playwright install --with-deps chromium
```

### 4. 准备配置

```bash
cp .env.example .env
cp config.example.json config.json
chmod 600 .env
```

编辑 `.env`，至少确认：

```dotenv
DOUYIN_STORAGE_STATE=storage-state.json
TASK_CONFIG=config.json
ARTIFACTS_DIR=artifacts
HEADLESS=true      # 服务器没有桌面，必须为 true
TRACE=false        # 失败追踪很吃内存和磁盘，2GB 机器建议关掉
```

### 5. 导入登录状态

服务器上没有桌面，**不要点面板里的「扫码登录」**（它会尝试打开有头浏览器）。

做法：在自己的电脑上打开桌面版扫码登录，然后把生成的 `storage-state.json` 通过面板「导入登录状态」上传；或直接复制到服务器项目根目录：

```bash
scp storage-state.json user@server:~/douyin-fire/storage-state.json
chmod 600 ~/douyin-fire/storage-state.json
```

> Cookie / 登录状态等同于账号凭证，别提交进 git，也别放在 Web 可访问的目录。

### 6. 启动面板

```bash
cd ~/douyin-fire
.venv/bin/python ui/server.py
```

面板只监听 `127.0.0.1:8765`。在自己的电脑上建 SSH 隧道访问：

```bash
ssh -L 8765:127.0.0.1:8765 user@server
# 然后浏览器打开 http://127.0.0.1:8765
```

想在服务器上常驻，用 systemd（把 `User`、路径改成你的）：

```ini
# /etc/systemd/system/douyin-fire-panel.service
[Unit]
Description=Douyin Fire panel
After=network-online.target

[Service]
Type=simple
User=douyin
WorkingDirectory=/home/douyin/douyin-fire
Environment=HEADLESS=true
ExecStart=/home/douyin/douyin-fire/.venv/bin/python ui/server.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now douyin-fire-panel
sudo journalctl -u douyin-fire-panel -f
```

### 7. 设置每日时间

在面板「自动任务」里选时间并「保存时间」。它会以**运行面板的那个用户**的身份写入 crontab：

```cron
*/30 * * * * ... HEADLESS=true /home/douyin/douyin-fire/.venv/bin/python run.py --catch-up --not-before 07:30 >> .../artifacts/scheduled.log 2>&1 # douyin-fire-local-schedule
```

所以：**systemd 里的 `User` 和面板里保存时间的用户要一致**，否则 cron 会写在另一个用户下。

验证：

```bash
crontab -l | grep douyin-fire-local-schedule
```

### 8. 2 核 2GB 小机器的注意事项

- 同时只会跑一个 Chromium（多账号是串行执行的），峰值内存约 0.8–1.2 GB，2GB 够用但不宽裕。
- 加 2GB swap 兜底：

  ```bash
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  ```

- 别再在这台机器上跑 MySQL / Java / 其他浏览器；也不要装桌面环境。
- 不建议用 Docker：Chromium 需要更大的 `/dev/shm`，容器开销会让内存更紧张。
- 云服务器 IP 访问抖音比家宽更容易触发安全验证。如果日志里频繁出现"要求进行安全验证"，可以考虑家宽代理，或改回「本机常开 + FRP 反代」的方案。
- 磁盘占用约 1 GB（Python 依赖 + Chromium），不需要仓库里 `dist/` 下的桌面版浏览器包。

---

## 停止或修改定时任务

- **改时间**：在面板「自动任务」里重新保存即可，旧条目会被替换。
- **完全停止**：

  ```bash
  crontab -l | grep -v 'douyin-fire-local-schedule' | crontab -
  ```

  这会删掉所有 `douyin-fire-local-schedule` 条目，不影响你其他的 crontab 任务。

## 常见问题

- **`crontab: command not found`**：没装 cron，按方式 A 第 3 步安装。
- **保存时间报"没有找到 crontab"**：同上，装好 cron 后重开面板。
- **面板点「立即发送」报找不到 Python**：`.venv` 不在项目根目录，或不是用 `python3 -m venv .venv` 创建的。
- **Chromium 启动失败、提示缺少库**：重新执行 `.venv/bin/playwright install --with-deps chromium`。
- **日志里"登录状态已失效"**：重新在本地登录并导入 `storage-state.json`。
- **好友被标记为"待核对"**：上一条消息结果不确定，为避免重复，当天不会自动重发，请在面板手动确认。
