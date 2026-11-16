const { app, BrowserWindow, dialog, shell } = require("electron");
const { spawn, execFile } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const isPackaged = app.isPackaged;
app.setName("Douyin Fire");
const projectRoot = path.resolve(__dirname, "..");
const defaultDataDir = process.platform === "darwin"
  ? path.join(os.homedir(), "Library", "Application Support", "Douyin Fire")
  : process.platform === "win32"
    ? path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "Douyin Fire")
    : path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"), "douyin-fire");
const dataDir = process.env.DOUYIN_DATA_DIR || (isPackaged ? defaultDataDir : projectRoot);
// An explicit connection keeps this desktop window on the existing Web service.
// Never fall back to a separate data directory when that service is unavailable.
function sharedPanelUrl() {
  const settingsPath = path.join(defaultDataDir, "desktop-connection.json");
  const configured = process.env.DOUYIN_PANEL_URL || (fs.existsSync(settingsPath)
    ? JSON.parse(fs.readFileSync(settingsPath, "utf8")).panelUrl : "");
  if (!configured) return null;
  const url = new URL(configured);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
      || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("共享面板地址必须是本机 HTTP 服务地址，例如 http://127.0.0.1:8765/");
  }
  return url.origin;
}
const backendName = process.platform === "win32" ? "douyin-fire-backend.exe" : "douyin-fire-backend";
const packagedBackend = path.join(process.resourcesPath, "backend", "douyin-fire-backend", backendName);
const developmentBackend = path.join(projectRoot, "dist", "backend", "douyin-fire-backend", backendName);
const python = process.env.DOUYIN_PYTHON || path.join(
  projectRoot,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
const backendScript = path.join(projectRoot, "desktop_backend.py");
const backendPath = isPackaged ? packagedBackend : (fs.existsSync(developmentBackend) ? developmentBackend : python);
const backendArgs = (...args) => backendPath === python ? [backendScript, ...args] : args;
const browserPath = isPackaged
  ? path.join(process.resourcesPath, "ms-playwright")
  : (process.env.PLAYWRIGHT_BROWSERS_PATH || path.join(projectRoot, "dist", "ms-playwright"));

let backend = null;
let mainWindow = null;
let quitting = false;
let readyUrl = null;
let outputBuffer = "";
let pendingScheduledRun = null;
const scheduledRun = process.argv.includes("--scheduled-run");

const ownsLock = app.requestSingleInstanceLock();
if (!ownsLock) app.quit();
else app.on("second-instance", (_event, argv = []) => {
  if (argv.includes("--scheduled-run")) {
    requestScheduledRun(scheduledRunPayload(argv));
    return;
  }
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

function backendEnvironment() {
  return {
    ...process.env,
    DOUYIN_DATA_DIR: dataDir,
    DOUYIN_DESKTOP_MODE: "1",
    DOUYIN_SCHEDULE_LAUNCHER: process.env.APPIMAGE || "",
    PLAYWRIGHT_BROWSERS_PATH: browserPath,
    UI_PORT: "0",
    PYTHONUNBUFFERED: "1",
  };
}

function writeBackendLog(chunk) {
  try {
    const logDir = path.join(dataDir, "logs");
    fs.mkdirSync(logDir, { recursive: true });
    fs.appendFileSync(path.join(logDir, "desktop-backend.log"), chunk);
  } catch (_) {
    // Logging must not prevent the desktop window from starting.
  }
}

function showBackendFailure(message) {
  if (quitting) return;
  dialog.showMessageBox({
    type: "error",
    title: "Douyin Fire 启动失败",
    message: "本地自动化服务没有启动。",
    detail: `${message}\n\n日志位置：${path.join(dataDir, "logs", "desktop-backend.log")}`,
  }).then(() => app.quit());
}

async function requestScheduledRun(payload) {
  if (!readyUrl) {
    pendingScheduledRun = payload;
    return;
  }
  try {
    const response = await fetch(`${readyUrl}/api/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "send-all", ...payload }),
    });
    if (!response.ok) writeBackendLog(`定时发送请求失败：HTTP ${response.status}\n`);
  } catch (error) {
    writeBackendLog(`定时发送请求失败：${error.message}\n`);
  }
}

// 定时任务会把补跑参数透传给后端；AppImage 第二次启动时也要保留它们。
function scheduledRunFlags(argv) {
  const flags = [];
  if (argv.includes("--catch-up")) flags.push("--catch-up");
  const index = argv.indexOf("--not-before");
  if (index !== -1 && argv[index + 1]) flags.push("--not-before", argv[index + 1]);
  return flags;
}

function scheduledRunPayload(argv) {
  const flags = scheduledRunFlags(argv);
  const payload = { catchUp: flags.includes("--catch-up") };
  const index = flags.indexOf("--not-before");
  if (index !== -1 && flags[index + 1]) payload.notBefore = flags[index + 1];
  return payload;
}

function stopBackend() {
  if (!backend || backend.killed) return;
  if (process.platform === "win32") {
    execFile("taskkill", ["/pid", String(backend.pid), "/T", "/F"], () => {});
  } else if (backend.pid) {
    try {
      process.kill(-backend.pid, "SIGTERM");
    } catch (_) {
      backend.kill("SIGTERM");
    }
  }
}

function startBackend() {
  if (!fs.existsSync(backendPath)) {
    throw new Error(`没有找到后端程序：${backendPath}\n请先构建后端，或重新安装桌面应用。`);
  }
  fs.mkdirSync(dataDir, { recursive: true });
  backend = spawn(backendPath, backendArgs("serve"), {
    cwd: dataDir,
    env: backendEnvironment(),
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
    detached: process.platform !== "win32",
  });

  backend.stdout.on("data", (chunk) => {
    const text = chunk.toString("utf8");
    writeBackendLog(text);
    outputBuffer += text;
    const match = outputBuffer.match(/LISTENING (http:\/\/127\.0\.0\.1:\d+)/);
    if (match && !readyUrl) {
      readyUrl = match[1];
      mainWindow.loadURL(readyUrl);
      if (pendingScheduledRun) {
        const payload = pendingScheduledRun;
        pendingScheduledRun = null;
        requestScheduledRun(payload);
      }
    }
    outputBuffer = outputBuffer.slice(-8192);
  });
  backend.stderr.on("data", (chunk) => writeBackendLog(chunk.toString("utf8")));
  backend.on("error", (error) => showBackendFailure(error.message));
  backend.on("close", (code, signal) => {
    if (!quitting) showBackendFailure(`后端退出，code=${code}, signal=${signal || "无"}`);
  });
}

async function connectSharedPanel(url) {
  try {
    const response = await fetch(`${url}/api/state`, { signal: AbortSignal.timeout(8000), redirect: "error" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const state = await response.json();
    if (!Array.isArray(state.accounts)) throw new Error("该地址不是续火花面板");
    readyUrl = url;
    if (scheduledRun) {
      await requestScheduledRun(scheduledRunPayload(process.argv));
      app.quit();
      return;
    }
    mainWindow.loadURL(readyUrl);
    if (pendingScheduledRun) {
      const payload = pendingScheduledRun;
      pendingScheduledRun = null;
      await requestScheduledRun(payload);
    }
  } catch (error) {
    showBackendFailure(`无法连接共用的 Web 服务：${url}\n${error.message}\n请先恢复 Web 服务后重新打开桌面端。`);
  }
}

function startScheduledBackend() {
  if (!fs.existsSync(backendPath)) {
    showBackendFailure(`没有找到后端程序：${backendPath}`);
    return;
  }
  fs.mkdirSync(dataDir, { recursive: true });
  backend = spawn(backendPath, backendArgs("run", ...scheduledRunFlags(process.argv)), {
    cwd: dataDir,
    env: backendEnvironment(),
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
    detached: process.platform !== "win32",
  });
  backend.stdout.on("data", (chunk) => writeBackendLog(chunk.toString("utf8")));
  backend.stderr.on("data", (chunk) => writeBackendLog(chunk.toString("utf8")));
  backend.on("error", (error) => showBackendFailure(error.message));
  backend.on("close", (code) => {
    quitting = true;
    app.exit(code || 0);
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 900,
    minWidth: 980,
    minHeight: 680,
    show: false,
    title: "Douyin Fire",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!readyUrl) return;
    try {
      if (new URL(url).origin !== new URL(readyUrl).origin) event.preventDefault();
    } catch (_) {
      event.preventDefault();
    }
  });
  mainWindow.on("closed", () => { mainWindow = null; });
  mainWindow.loadURL("data:text/html,<meta charset=utf-8><title>Douyin Fire</title><p style='font:16px sans-serif;padding:32px'>正在启动本地服务…</p>");
}

app.on("before-quit", () => {
  quitting = true;
  stopBackend();
});

if (ownsLock) app.whenReady().then(() => {
  try {
    const panelUrl = sharedPanelUrl();
    if (panelUrl) {
      if (!scheduledRun) createWindow();
      connectSharedPanel(panelUrl);
      return;
    }
    if (scheduledRun) {
      startScheduledBackend();
      return;
    }
    createWindow();
    startBackend();
  } catch (error) {
    showBackendFailure(error.message);
  }
});

app.on("window-all-closed", () => app.quit());
