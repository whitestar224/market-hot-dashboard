const { app, BrowserWindow, Menu, dialog, shell } = require("electron");
const childProcess = require("child_process");
const fs = require("fs");
const net = require("net");
const path = require("path");

let mainWindow = null;
let backendProcess = null;
let backendPort = null;
let backendUrl = null;

const PRODUCT_NAME = "星云社";
const HEALTH_TIMEOUT_MS = 30000;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function fileExists(target) {
  try {
    return fs.existsSync(target);
  } catch (_) {
    return false;
  }
}

const DEFAULT_DESKTOP_ENV = {
  XINGYUN_ENV: "desktop",
  XINGYUN_LOAD_ENV_EXAMPLE: "0",
  XINGYUN_DISABLE_DESKTOP_ALERT: "0",
  OKX_PRODUCT_TYPE: "futures",
  OKX_FUTURES_TYPE: "USDT",
  OKX_COUNTRY_FILTER: "1",
  OKX_RANK_ZONE: "utc24",
  OKX_RANK_PAGE_SIZE: "25",
  OKX_DESKTOP_PROXY_ENABLED: "auto",
  OKX_DESKTOP_PROXY_CONNECT_HOST: "127.0.0.1",
  OKX_DESKTOP_PROXY_PORTS: "17000,17001,17002,17003,17004,17005",
  OKX_DESKTOP_PROXY_HOST: "www.okx.com",
  OKX_DESKTOP_USER_AGENT: "OKX/2.6.1",
  OKX_DESKTOP_PROXY_TIMEOUT: "3",
  OKX_ENABLE_WS: "1",
  OKX_FUTURES_CACHE_MAX_AGE_HOURS: "168"
};

function readTextFile(target) {
  try {
    if (!fileExists(target)) {
      return "";
    }
    return fs.readFileSync(target, "utf8");
  } catch (_) {
    return "";
  }
}

function parseEnvText(text) {
  const parsed = {};
  for (const rawLine of String(text || "").split(/\r?\n/)) {
    const line = rawLine.replace(/^\uFEFF/, "").trim();
    if (!line || line.startsWith("#") || !line.includes("=")) {
      continue;
    }
    const index = line.indexOf("=");
    const key = line.slice(0, index).trim();
    const value = line.slice(index + 1).trim();
    if (key) {
      parsed[key] = value;
    }
  }
  return parsed;
}

function writeEnvFile(target, values) {
  const keys = Object.keys(values).sort();
  const lines = ["# XingyunShe desktop runtime config"];
  for (const key of keys) {
    if (values[key] !== undefined && values[key] !== null) {
      lines.push(`${key}=${values[key]}`);
    }
  }
  lines.push("");
  fs.writeFileSync(target, lines.join("\n"), "utf8");
}

function hasUsefulSeedCache(fileName, target) {
  try {
    const payload = JSON.parse(fs.readFileSync(target, "utf8"));
    if (fileName === "okx_futures_hot.json") {
      return Array.isArray(payload.rows) && payload.rows.length >= 10;
    }
    if (fileName.startsWith("api_")) {
      return Array.isArray(payload.sources) || Array.isArray(payload.sections) || Array.isArray(payload.items);
    }
    return true;
  } catch (_) {
    return false;
  }
}

function seedRuntimeCache(runtimeDir) {
  if (!app.isPackaged) {
    return;
  }
  const seedRoot = path.join(process.resourcesPath, "runtime-seed");
  if (!fileExists(seedRoot)) {
    return;
  }
  for (const fileName of fs.readdirSync(seedRoot)) {
    const source = path.join(seedRoot, fileName);
    const target = path.join(runtimeDir, fileName);
    try {
      const stat = fs.statSync(source);
      if (!stat.isFile()) {
        continue;
      }
      const targetIsUseful = fileExists(target) && hasUsefulSeedCache(fileName, target);
      if (!targetIsUseful || fs.statSync(target).mtimeMs < stat.mtimeMs) {
        fs.copyFileSync(source, target);
      }
    } catch (_) {
      // Cache seeding is best effort; live data can still refresh it later.
    }
  }
}

function projectRoot() {
  return path.resolve(__dirname, "..");
}

function dashboardRoot() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "dashboard");
  }
  return projectRoot();
}

function userConfigPaths() {
  const userData = app.getPath("userData");
  return {
    userData,
    configDir: path.join(userData, "config"),
    envPath: path.join(userData, "config", ".env"),
    runtimeDir: path.join(userData, "runtime")
  };
}

function ensureDesktopDirectories() {
  const paths = userConfigPaths();
  fs.mkdirSync(paths.configDir, { recursive: true });
  fs.mkdirSync(paths.runtimeDir, { recursive: true });
  if (app.isPackaged) {
    const bundledEnv = parseEnvText(readTextFile(path.join(dashboardRoot(), ".env.desktop")));
    const sidecarEnv = parseEnvText(readTextFile(path.join(path.dirname(process.execPath), ".env")));
    const existingEnv = parseEnvText(readTextFile(paths.envPath));
    const merged = { ...DEFAULT_DESKTOP_ENV, ...bundledEnv, ...sidecarEnv, ...existingEnv };
    for (const [key, value] of Object.entries({ ...bundledEnv, ...sidecarEnv })) {
      if (!String(existingEnv[key] || "").trim() && String(value || "").trim()) {
        merged[key] = value;
      }
    }
    writeEnvFile(paths.envPath, merged);
    seedRuntimeCache(paths.runtimeDir);
  }
  return paths;
}

function resolveBackendExecutable() {
  if (!app.isPackaged) {
    return null;
  }
  const backendRoot = path.join(process.resourcesPath, "backend");
  const candidates =
    process.platform === "win32"
      ? [
          path.join(backendRoot, "xingyunshe-server", "xingyunshe-server.exe"),
          path.join(backendRoot, "xingyunshe-server.exe")
        ]
      : [
          path.join(backendRoot, "xingyunshe-server", "xingyunshe-server"),
          path.join(backendRoot, "xingyunshe-server")
        ];
  return candidates.find(fileExists) || null;
}

function pythonCandidates() {
  const configured = process.env.XINGYUN_PYTHON;
  const candidates = [];
  if (configured) {
    candidates.push({ command: configured, prefix: [] });
  }
  if (process.platform === "win32") {
    candidates.push({ command: "python", prefix: [] });
    candidates.push({ command: "py", prefix: ["-3"] });
  } else {
    candidates.push({ command: "python3", prefix: [] });
    candidates.push({ command: "python", prefix: [] });
  }
  return candidates;
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = address && typeof address === "object" ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

async function waitForHealth(url) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < HEALTH_TIMEOUT_MS) {
    try {
      const response = await fetch(`${url}/api/health`, { cache: "no-store" });
      if (response.ok) {
        return true;
      }
    } catch (_) {
      // Server is still booting.
    }
    await sleep(350);
  }
  return false;
}

function spawnProcess(command, args, options) {
  const child = childProcess.spawn(command, args, {
    ...options,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"]
  });
  child.stdout?.on("data", (chunk) => console.log(`[backend] ${chunk}`.trimEnd()));
  child.stderr?.on("data", (chunk) => console.error(`[backend] ${chunk}`.trimEnd()));
  return child;
}

async function startBackend() {
  const root = dashboardRoot();
  const config = ensureDesktopDirectories();
  backendPort = await getFreePort();
  backendUrl = `http://127.0.0.1:${backendPort}`;

  const backendExe = resolveBackendExecutable();
  const commonArgs = ["--host", "127.0.0.1", "--port", String(backendPort)];
  const env = {
    ...process.env,
    XINGYUN_APP_ROOT: root,
    XINGYUN_RUNTIME_DIR: app.isPackaged ? config.runtimeDir : process.env.XINGYUN_RUNTIME_DIR || path.join(root, ".runtime-cache"),
    XINGYUN_ENV: process.env.XINGYUN_ENV || "desktop",
    XINGYUN_HOST: "127.0.0.1",
    PORT: String(backendPort),
    XINGYUN_PUBLIC_BASE_URL: backendUrl,
    XINGYUN_COOKIE_SECURE: "0",
    XINGYUN_DISABLE_DESKTOP_ALERT: process.env.XINGYUN_DISABLE_DESKTOP_ALERT || "0"
  };
  if (app.isPackaged) {
    env.XINGYUN_ENV_FILE = config.envPath;
    env.XINGYUN_LOAD_DOTENV = "0";
    env.XINGYUN_LOAD_ENV_EXAMPLE = "0";
  }

  if (backendExe) {
    backendProcess = spawnProcess(backendExe, commonArgs, { cwd: root, env });
  } else {
    const serverPath = path.join(root, "server.py");
    let lastError = null;
    for (const candidate of pythonCandidates()) {
      try {
        backendProcess = spawnProcess(candidate.command, [...candidate.prefix, serverPath, ...commonArgs], { cwd: root, env });
        break;
      } catch (error) {
        lastError = error;
      }
    }
    if (!backendProcess) {
      throw lastError || new Error("Cannot find Python runtime for desktop backend.");
    }
  }

  const healthy = await waitForHealth(backendUrl);
  if (!healthy) {
    throw new Error("星云社本地服务启动超时。");
  }
  return backendUrl;
}

function stopBackend() {
  if (!backendProcess || backendProcess.killed) {
    return;
  }
  const pid = backendProcess.pid;
  try {
    if (process.platform === "win32" && pid) {
      childProcess.spawn("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true });
    } else {
      backendProcess.kill("SIGTERM");
    }
  } catch (_) {
    try {
      backendProcess.kill();
    } catch (_) {
      // Ignore shutdown races.
    }
  }
  backendProcess = null;
}

function desktopPlatformQuery() {
  return process.platform === "darwin" ? "macos" : "windows";
}

function createMenu() {
  const template = [
    {
      label: PRODUCT_NAME,
      submenu: [
        { role: "reload", label: "刷新" },
        { role: "toggleDevTools", label: "开发者工具" },
        { type: "separator" },
        { role: "quit", label: "退出" }
      ]
    },
    {
      label: "编辑",
      submenu: [
        { role: "undo", label: "撤销" },
        { role: "redo", label: "重做" },
        { type: "separator" },
        { role: "cut", label: "剪切" },
        { role: "copy", label: "复制" },
        { role: "paste", label: "粘贴" }
      ]
    },
    {
      label: "视图",
      submenu: [
        { role: "zoomIn", label: "放大" },
        { role: "zoomOut", label: "缩小" },
        { role: "resetZoom", label: "实际大小" },
        { type: "separator" },
        { role: "togglefullscreen", label: "全屏" }
      ]
    }
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function createWindow() {
  const url = await startBackend();
  createMenu();
  mainWindow = new BrowserWindow({
    width: process.platform === "darwin" ? 1520 : 1600,
    height: 980,
    minWidth: 1180,
    minHeight: 760,
    title: PRODUCT_NAME,
    backgroundColor: "#0b0f0b",
    icon: path.join(dashboardRoot(), "assets", "icon-512.png"),
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });

  mainWindow.webContents.setWindowOpenHandler(({ url: targetUrl }) => {
    if (targetUrl.startsWith(url)) {
      return { action: "allow" };
    }
    shell.openExternal(targetUrl);
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, targetUrl) => {
    if (targetUrl.startsWith(url)) {
      return;
    }
    event.preventDefault();
    shell.openExternal(targetUrl);
  });

  await mainWindow.loadURL(`${url}/index.html?desktop=${desktopPlatformQuery()}`);
}

app.whenReady().then(createWindow).catch((error) => {
  console.error(error);
  dialog.showErrorBox("星云社启动失败", error.message || String(error));
  app.quit();
});

app.on("window-all-closed", () => {
  stopBackend();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", stopBackend);

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow().catch((error) => dialog.showErrorBox("星云社启动失败", error.message || String(error)));
  }
});
