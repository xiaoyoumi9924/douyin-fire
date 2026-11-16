const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// Exercise the real desktop entry point without launching Electron, a browser,
// a backend process, or sending network requests.
function desktop(argv = ["Douyin Fire"], connection = null, online = true) {
  const handlers = new Map();
  const ready = [];
  const requests = [];
  const spawns = [];
  const urls = [];
  const errors = [];
  const app = {
    isPackaged: true,
    setName() {},
    requestSingleInstanceLock: () => true,
    on: (event, callback) => handlers.set(event, callback),
    whenReady: () => ({ then: (callback) => ready.push(callback) }),
    quit() {},
  };
  class Window {
    constructor() {
      this.webContents = { setWindowOpenHandler() {}, on() {} };
    }
    once() {}
    on() {}
    loadURL(url) { urls.push(url); }
  }
  const modules = {
    electron: { app, BrowserWindow: Window, dialog: { showMessageBox: (message) => { errors.push(message); return Promise.resolve(); } }, shell: {} },
    "node:child_process": {
      spawn: (executable, args) => {
        const child = new EventEmitter();
        child.stdout = new EventEmitter();
        child.stderr = new EventEmitter();
        spawns.push({ executable, args, child });
        return child;
      },
    },
    "node:fs": {
      existsSync: (filename) => filename.endsWith("desktop-connection.json") ? connection !== null : true,
      readFileSync: () => JSON.stringify(connection),
      mkdirSync() {}, appendFileSync() {},
    },
    "node:os": { homedir: () => "/home/test" },
    "node:path": path,
  };
  const context = {
    require: (name) => {
      assert.ok(name in modules, `Unexpected module: ${name}`);
      return modules[name];
    },
    __dirname: path.resolve(__dirname, "../desktop"),
    process: { argv, platform: "linux", env: {}, resourcesPath: "/app/resources" },
    fetch: async (url, options) => {
      if (url.endsWith("/api/state")) return { ok: online, status: 503, json: async () => ({ accounts: [] }) };
      requests.push({ url, body: JSON.parse(options.body) });
      return { ok: true };
    },
    URL,
    AbortSignal,
  };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, "../desktop/main.cjs"), "utf8"), context);
  ready[0]();
  return {
    requests,
    spawns,
    urls,
    errors,
    second: (args) => handlers.get("second-instance")({}, args),
    listening: () => spawns[0].child.stdout.emit("data", Buffer.from("LISTENING http://127.0.0.1:8765\n")),
  };
}

test("open window forwards the incoming schedule rather than primary argv", () => {
  const runtime = desktop(["Douyin Fire", "--not-before", "11:00"]);
  runtime.listening();
  runtime.second(["Douyin Fire", "--scheduled-run", "--catch-up", "--not-before", "08:05"]);
  assert.deepEqual(runtime.requests, [{
    url: "http://127.0.0.1:8765/api/action",
    body: { action: "send-all", catchUp: true, notBefore: "08:05" },
  }]);
});

test("schedule arriving before backend readiness retains its parameters", () => {
  const runtime = desktop();
  runtime.second(["Douyin Fire", "--scheduled-run", "--catch-up", "--not-before", "23:59"]);
  assert.equal(runtime.requests.length, 0);
  runtime.listening();
  runtime.listening();
  assert.deepEqual(runtime.requests.map((request) => request.body), [
    { action: "send-all", catchUp: true, notBefore: "23:59" },
  ]);
});

test("cold scheduled startup sends the flags to the backend command", () => {
  const runtime = desktop(["Douyin Fire", "--scheduled-run", "--catch-up", "--not-before", "08:05"]);
  assert.deepEqual(Array.from(runtime.spawns[0].args), ["run", "--catch-up", "--not-before", "08:05"]);
  assert.equal(runtime.requests.length, 0);
});

test("ordinary startup does not request a scheduled run", () => {
  const runtime = desktop();
  runtime.listening();
  assert.equal(runtime.requests.length, 0);
  assert.deepEqual(Array.from(runtime.spawns[0].args), ["serve"]);
});

test("shared desktop uses Web service without starting another backend", async () => {
  const runtime = desktop(undefined, { panelUrl: "http://127.0.0.1:8765/" });
  await new Promise(setImmediate);
  assert.equal(runtime.spawns.length, 0);
  assert.equal(runtime.urls.at(-1), "http://127.0.0.1:8765");
  assert.equal(runtime.requests.length, 0);
});

test("offline shared service does not fall back to old desktop data", async () => {
  const runtime = desktop(undefined, { panelUrl: "http://127.0.0.1:8765/" }, false);
  await new Promise(setImmediate);
  assert.equal(runtime.spawns.length, 0);
  assert.equal(runtime.errors.length, 1);
  assert.match(runtime.errors[0].detail, /无法连接共用的 Web 服务/);
});

test("shared service rejects nonlocal addresses", () => {
  const runtime = desktop(undefined, { panelUrl: "http://example.com/" });
  assert.equal(runtime.spawns.length, 0);
  assert.equal(runtime.errors.length, 1);
});

test("scheduled startup on shared service forwards once without a local runner", async () => {
  const runtime = desktop(["Douyin Fire", "--scheduled-run", "--catch-up", "--not-before", "08:05"], { panelUrl: "http://127.0.0.1:8765/" });
  await new Promise(setImmediate);
  assert.equal(runtime.spawns.length, 0);
  assert.deepEqual(runtime.requests, [{ url: "http://127.0.0.1:8765/api/action", body: { action: "send-all", catchUp: true, notBefore: "08:05" } }]);
});
