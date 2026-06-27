// Electron メインプロセス。Overwolf の「ウィンドウ・GEP・C#プラグイン起動」を置き換える。
//   - home / overlay の2ウィンドウを管理
//   - Pythonワーカーを child_process で起動/停止（旧 C#プラグイン）
//   - ライブクライアントデータ(127.0.0.1:2999)をポーリング（旧 GEP）
//   - チーム設定/ワーカー設定をファイルで保持し、レンダラーへIPCで橋渡し
const { app, BrowserWindow, ipcMain, screen } = require('electron');
const path = require('path');
const fs = require('fs');
const https = require('https');
const http = require('http');
const { spawn } = require('child_process');

const ROOT = path.join(__dirname, '..');

// ── 設定ファイル（旧 localStorage 共有の置き換え） ──
let CONFIG_DIR = ROOT;  // app.ready 後に userData へ
const teamsFile = () => path.join(CONFIG_DIR, '4v4wars_teams.json');
const settingsFile = () => path.join(CONFIG_DIR, '4v4wars_worker.json');

function readJson(file, def) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (e) { return def; }
}
function writeJson(file, data) {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(data, null, 2));
  } catch (e) { console.error('[4v4Wars] 設定保存に失敗:', e); }
}

let homeWin = null;
let overlayWin = null;
let workerProc = null;
let gameActive = false;

// ── ウィンドウ ──
function createHomeWindow() {
  homeWin = new BrowserWindow({
    width: 780,
    height: 800,
    frame: false,
    resizable: false,
    show: true,
    backgroundColor: '#1a1a2e',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  homeWin.loadFile(path.join(ROOT, 'home.html'));
  homeWin.on('closed', () => { homeWin = null; });
}

function createOverlayWindow() {
  if (overlayWin) return;
  const disp = screen.getPrimaryDisplay();
  const { x, y, width, height } = disp.bounds;
  overlayWin = new BrowserWindow({
    x, y, width, height,
    transparent: true,
    frame: false,
    resizable: false,
    movable: false,
    focusable: false,
    skipTaskbar: true,
    hasShadow: false,
    alwaysOnTop: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  overlayWin.setIgnoreMouseEvents(true);            // クリックスルー
  overlayWin.setAlwaysOnTop(true, 'screen-saver');  // ゲームより前面
  overlayWin.loadFile(path.join(ROOT, 'in_game.html'));
  overlayWin.on('closed', () => { overlayWin = null; });
}

function destroyOverlayWindow() {
  if (overlayWin) { overlayWin.close(); overlayWin = null; }
}

function broadcast(channel, payload) {
  BrowserWindow.getAllWindows().forEach((w) => w.webContents.send(channel, payload));
}

// ── Pythonワーカー（旧 C#プラグイン） ──
function launchWorker() {
  if (workerProc) return;
  const s = readJson(settingsFile(), {});
  if (!s.workerDir) {
    console.log('[4v4Wars] ワーカーフォルダ未設定（ホーム画面の設定で指定してください）');
    return;
  }
  const py = s.pythonCmd || 'pythonw';
  const script = path.join(s.workerDir, 'worker.py');
  const env = { ...process.env };
  if (s.tesseractCmd) env.TESSERACT_CMD = s.tesseractCmd;
  try {
    workerProc = spawn(py, [script], { cwd: s.workerDir, env, windowsHide: true });
    workerProc.on('exit', () => { workerProc = null; });
    workerProc.on('error', (e) => { console.error('[4v4Wars] ワーカー起動失敗:', e.message); workerProc = null; });
    console.log('[4v4Wars] ワーカーを起動しました');
    pushRoster();  // 現在のロスターをワーカーへ送る（config.jsonより優先）
  } catch (e) {
    console.error('[4v4Wars] ワーカー起動例外:', e);
  }
}

function killWorker() {
  if (workerProc) { try { workerProc.kill(); } catch (e) { /* ignore */ } workerProc = null; }
}

// ホームのロスター（チーム設定）からプレイヤー名だけをワーカーへ送る（成功までリトライ）
function pushRoster(attempt = 0) {
  const data = readJson(teamsFile(), null);
  if (!data) return;
  const names = [];
  ['teamA', 'teamB'].forEach((k) => {
    (data[k]?.members || []).forEach((m) => {
      const n = typeof m === 'string' ? m : m.name;
      if (n) names.push(n);
    });
  });
  if (!names.length) return;
  const body = JSON.stringify({ names });
  const req = http.request(
    { host: '127.0.0.1', port: 17653, path: '/config', method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } },
    (res) => { res.resume(); },
  );
  req.on('error', () => { if (attempt < 20) setTimeout(() => pushRoster(attempt + 1), 2000); });
  req.write(body);
  req.end();
}

// ── ライブクライアントデータ ポーリング（旧 GEP） ──
function pollLiveClient() {
  const req = https.request(
    { host: '127.0.0.1', port: 2999, path: '/liveclientdata/allgamedata',
      method: 'GET', rejectUnauthorized: false, timeout: 2000 },
    (res) => {
      let buf = '';
      res.on('data', (c) => { buf += c; });
      res.on('end', () => {
        try {
          const data = JSON.parse(buf);
          const players = data.allPlayers || [];
          setGameActive(true);
          if (overlayWin) overlayWin.webContents.send('players', players);
        } catch (e) {
          setGameActive(false);  // 試合外（JSONでない応答など）
        }
      });
    },
  );
  req.on('error', () => setGameActive(false));     // ゲーム未起動/試合外
  req.on('timeout', () => { req.destroy(); setGameActive(false); });
  req.end();
}

function setGameActive(active) {
  if (active === gameActive) return;
  gameActive = active;
  broadcast('game-active', active);
  if (active) {
    createOverlayWindow();
    launchWorker();
  } else {
    killWorker();
    destroyOverlayWindow();
  }
}

// ── IPC ──
ipcMain.handle('get-teams', () => readJson(teamsFile(), null));
ipcMain.handle('set-teams', (_e, data) => {
  writeJson(teamsFile(), data);
  broadcast('teams', data);  // オーバーレイへ即反映
  if (workerProc) pushRoster();
  return true;
});
ipcMain.handle('get-worker-settings', () => readJson(settingsFile(), null));
ipcMain.handle('set-worker-settings', (_e, data) => {
  writeJson(settingsFile(), data);
  // 試合中に設定が変わったらワーカーを入れ直す
  if (gameActive) { killWorker(); launchWorker(); }
  return true;
});
ipcMain.on('win-minimize', (e) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  if (w) w.minimize();
});
ipcMain.on('app-quit', () => { killWorker(); app.quit(); });

// ── ライフサイクル ──
app.whenReady().then(() => {
  CONFIG_DIR = app.getPath('userData');
  createHomeWindow();
  setInterval(pollLiveClient, 2000);
  pollLiveClient();
});

app.on('window-all-closed', () => { killWorker(); app.quit(); });
app.on('before-quit', () => killWorker());
