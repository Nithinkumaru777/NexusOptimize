const { app, BrowserWindow, dialog, globalShortcut, ipcMain, screen, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const http = require('http');
const crypto = require('crypto');
const { overlayBounds } = require('./overlay-bounds');

let mainWindow;
let pythonProcess;
let backendReady = false;

const BACKEND_PORT = 5000;
const ROOT = path.join(__dirname, '..', '..');
// Per-session secret: the core rejects every request (except /health) without it.
const SESSION_TOKEN = crypto.randomBytes(32).toString('hex');

// ---------------------------------------------------------------------------
// Single instance lock — prevents a second, invisible copy from spawning
// silently in the background when the app is launched twice.
// ---------------------------------------------------------------------------
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', (_e, argv) => {
    forwardNxm(argv);
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: 1040,
    minHeight: 680,
    show: false, // wait for ready-to-show to avoid white/blank flash
    backgroundColor: '#0b0d14',
    frame: false,
    titleBarStyle: 'hidden',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false
    }
  });

  mainWindow.loadFile(path.join(__dirname, '..', 'index.html'));
  // App launched by an nxm:// link while it was closed.
  mainWindow.webContents.once('did-finish-load', () => forwardNxm(process.argv));

  // Primary path: show once the renderer has actually painted a frame.
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    mainWindow.focus();
    mainWindow.moveTop();
  });

  // Defensive fallback: if 'ready-to-show' is ever swallowed (renderer error,
  // slow first paint, etc.) force the window on-screen anyway instead of
  // leaving a hidden process running with no visible UI.
  setTimeout(() => {
    if (mainWindow && !mainWindow.isVisible()) {
      console.warn('[NOM] ready-to-show did not fire in time — forcing window visible.');
      mainWindow.show();
      mainWindow.focus();
    }
  }, 4000);

  // Test evidence for docs/TESTLOG.md: NEXUS_SCREENSHOT=<png path> saves the
  // rendered window 10 s after load (after telemetry has polled a few times).
  if (process.env.NEXUS_SCREENSHOT) {
    mainWindow.webContents.once('did-finish-load', () => setTimeout(async () => {
      const img = await mainWindow.webContents.capturePage();
      require('fs').writeFileSync(process.env.NEXUS_SCREENSHOT, img.toPNG());
      console.log(`[NOM] screenshot saved: ${process.env.NEXUS_SCREENSHOT}`);
    }, 10000));
  }

  mainWindow.webContents.on('did-fail-load', (_e, code, desc) => {
    console.error(`[NOM] Renderer failed to load: ${desc} (${code})`);
    mainWindow.show();
  });

  mainWindow.webContents.on('render-process-gone', (_e, details) => {
    console.error('[NOM] Renderer process gone:', details.reason);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
    // The overlay is a second window; without this the app would keep running.
    if (overlayWindow) overlayWindow.destroy();
  });

  // Open any external links in the OS browser instead of a new Electron window.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

// ---------------------------------------------------------------------------
// Backend lifecycle
// ---------------------------------------------------------------------------
function startBackend() {
  const env = { ...process.env, NEXUS_TOKEN: SESSION_TOKEN, NEXUS_PORT: String(BACKEND_PORT) };
  let command, args, cwd;
  if (app.isPackaged) {
    // Production: PyInstaller build (npm run build:backend) shipped as an extraResource.
    cwd = path.join(process.resourcesPath, 'backend');
    command = path.join(cwd, 'nexus-core.exe');
    args = [];
    // The frozen core can't see the repo's third_party/, so point it at the bundled PresentMon.
    const presentMon = path.join(process.resourcesPath, 'third_party', 'PresentMon.exe');
    if (!env.NEXUS_PRESENTMON && fs.existsSync(presentMon)) env.NEXUS_PRESENTMON = presentMon;
  } else {
    const isWin = process.platform === 'win32';
    command = path.join(ROOT, '.venv', 'Scripts', isWin ? 'python.exe' : 'python');
    args = ['-m', 'core.main'];
    cwd = ROOT;
  }

  pythonProcess = spawn(command, args, { cwd, env, windowsHide: true });

  pythonProcess.stdout.on('data', (data) => {
    console.log(`[backend] ${data}`.trim());
  });

  pythonProcess.stderr.on('data', (data) => {
    console.error(`[backend:err] ${data}`.trim());
  });

  pythonProcess.on('error', (err) => {
    console.error('[NOM] Failed to spawn backend process:', err);
    // No console in the installed app, so surface it instead of a silently dead UI.
    if (app.isPackaged) dialog.showErrorBox('Nexus Optimize & Mod', `Could not start the backend:\n${err.message}`);
  });

  pythonProcess.on('exit', (code) => {
    console.log(`[NOM] Backend exited with code ${code}`);
    backendReady = false;
  });

  waitForBackend();
}

function waitForBackend(attempt = 0) {
  const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/health`, (res) => {
    if (res.statusCode === 200) {
      backendReady = true;
      if (mainWindow) mainWindow.webContents.send('backend-status', { ready: true });
      return;
    }
    retry(attempt);
  });
  req.on('error', () => retry(attempt));
}

function retry(attempt) {
  if (attempt > 40) {
    console.error('[NOM] Backend did not become ready after repeated attempts.');
    if (mainWindow) mainWindow.webContents.send('backend-status', { ready: false });
    return;
  }
  setTimeout(() => waitForBackend(attempt + 1), 500);
}

// ---------------------------------------------------------------------------
// IPC — custom titlebar window controls (renderer has no direct Node access)
// ---------------------------------------------------------------------------
ipcMain.on('window:minimize', () => mainWindow && mainWindow.minimize());
ipcMain.on('window:maximize', () => {
  if (!mainWindow) return;
  if (mainWindow.isMaximized()) mainWindow.unmaximize();
  else mainWindow.maximize();
});
ipcMain.on('window:close', () => mainWindow && mainWindow.close());
ipcMain.handle('backend:port', () => BACKEND_PORT);
ipcMain.handle('backend:token', () => SESSION_TOKEN);

// ---------------------------------------------------------------------------
// In-game overlay: a separate transparent, click-through, always-on-top window.
// No injection into the game - so it shows over borderless/windowed games,
// not over exclusive fullscreen (SPEC 4.4).
// ---------------------------------------------------------------------------
let overlayWindow = null;
const OVERLAY_HOTKEY = 'CommandOrControl+Shift+O';

function toggleOverlay() {
  const visible = !(overlayWindow && overlayWindow.isVisible());
  if (!overlayWindow) {
    overlayWindow = new BrowserWindow({
      ...overlayBounds(screen.getPrimaryDisplay().workArea),
      transparent: true, frame: false, resizable: false, movable: false, focusable: false,
      skipTaskbar: true, hasShadow: false, alwaysOnTop: true, show: false,
      webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true,
                        nodeIntegration: false, sandbox: true, backgroundThrottling: false }
    });
    overlayWindow.setAlwaysOnTop(true, 'screen-saver');
    overlayWindow.setIgnoreMouseEvents(true);
    overlayWindow.loadFile(path.join(__dirname, '..', 'overlay.html'));
    overlayWindow.once('ready-to-show', () => overlayWindow.showInactive());
    overlayWindow.on('closed', () => { overlayWindow = null; });
  } else if (visible) {
    overlayWindow.showInactive();
  } else {
    overlayWindow.hide();
  }
  if (mainWindow) mainWindow.webContents.send('overlay-state', visible);
  return visible;
}

ipcMain.handle('overlay:toggle', () => toggleOverlay());

// Keep the HUD pinned to the corner when resolution, scaling or the taskbar changes.
function repinOverlay() {
  if (overlayWindow) overlayWindow.setBounds(overlayBounds(screen.getPrimaryDisplay().workArea));
}

// File pickers for the live mod applier. The renderer names a kind; the
// filters live here so the renderer can't widen them.
const PICKERS = {
  pak: { properties: ['openFile', 'multiSelections'], filters: [{ name: 'Unreal pak mod', extensions: ['pak', 'utoc', 'ucas', 'sig'] }] },
  reshade: { properties: ['openFile'], filters: [{ name: 'ReShade preset', extensions: ['ini'] }] },
  config: { properties: ['openFile'], filters: [{ name: 'Config file', extensions: ['ini', 'cfg', 'json', 'xml', 'txt'] }] },
  archive: { properties: ['openFile'], filters: [{ name: 'Mod archive', extensions: ['zip', '7z', 'rar'] }] },
  folder: { properties: ['openDirectory'] }
};
ipcMain.handle('dialog:pick', async (_e, kind, defaultPath) => {
  const opts = PICKERS[kind];
  if (!opts || !mainWindow) return [];
  const res = await dialog.showOpenDialog(mainWindow, {
    ...opts, defaultPath: typeof defaultPath === 'string' ? defaultPath : undefined
  });
  return res.canceled ? [] : res.filePaths;
});

// ---------------------------------------------------------------------------
// nxm:// links (Nexus "Mod Manager Download"). Opt-in from the Mods screen,
// because registering takes the link over from Vortex / Mod Organizer 2.
// ---------------------------------------------------------------------------
const NXM_ARGS = app.isPackaged ? [] : [path.resolve(process.argv[1] || '.')];

function forwardNxm(argv) {
  const url = argv.find((a) => typeof a === 'string' && a.toLowerCase().startsWith('nxm://'));
  if (url && mainWindow) mainWindow.webContents.send('nxm-link', url);
}

ipcMain.handle('nxm:status', () => app.isDefaultProtocolClient('nxm', process.execPath, NXM_ARGS));
ipcMain.handle('nxm:register', (_e, on) => (on
  ? app.setAsDefaultProtocolClient('nxm', process.execPath, NXM_ARGS)
  : app.removeAsDefaultProtocolClient('nxm', process.execPath, NXM_ARGS)));

// Mod pages open in the user's browser; only nexusmods.com https URLs are allowed.
ipcMain.handle('nexus:open', (_e, url) => {
  try {
    const u = new URL(url);
    if (u.protocol === 'https:' && (u.hostname === 'www.nexusmods.com' || u.hostname === 'nexusmods.com')) shell.openExternal(u.href);
  } catch { /* not a URL */ }
});

app.on('ready', () => {
  startBackend();
  createWindow();
  screen.on('display-metrics-changed', repinOverlay);
  screen.on('display-added', repinOverlay);
  screen.on('display-removed', repinOverlay);
  if (!globalShortcut.register(OVERLAY_HOTKEY, toggleOverlay)) {
    console.warn(`[NOM] ${OVERLAY_HOTKEY} is taken by another app - overlay hotkey disabled.`);
  }
});

app.on('will-quit', () => globalShortcut.unregisterAll());

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

// kill() skips the core's shutdown hook, so turn Process Assassin off first:
// background apps it dropped to Idle must not stay there after we exit.
let assassinRestored = false;
app.on('before-quit', (e) => {
  if (assassinRestored || !backendReady) return;
  e.preventDefault();
  assassinRestored = true;
  const done = () => app.quit();
  const req = http.request({
    host: '127.0.0.1', port: BACKEND_PORT, path: '/optimize/assassin', method: 'POST', timeout: 2000,
    headers: { 'Content-Type': 'application/json', 'X-Nexus-Token': SESSION_TOKEN },
  }, (res) => { res.resume(); res.on('end', done); });
  req.on('error', done);
  req.on('timeout', () => req.destroy());
  req.end(JSON.stringify({ enabled: false }));
});

app.on('quit', () => {
  if (pythonProcess) pythonProcess.kill();
});
