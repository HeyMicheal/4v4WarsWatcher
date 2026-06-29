// Electron メインプロセス。Overwolf の「ウィンドウ・GEP・C#プラグイン起動」を置き換える。
//   - home / overlay の2ウィンドウを管理
//   - Pythonワーカーを child_process で起動/停止（旧 C#プラグイン）
//   - ライブクライアントデータ(127.0.0.1:2999)をポーリング（旧 GEP）
//   - チーム設定/ワーカー設定をファイルで保持し、レンダラーへIPCで橋渡し
//   - 配信用HTTPサーバ(127.0.0.1:17654)で、OBSブラウザソース用オーバーレイを配信
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
let latestPlayers = [];  // ライブクライアントデータの最新 allPlayers（配信サーバ /players 用）
let streamServer = null;  // 配信用HTTPサーバ（終了時に明示クローズする）

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
  lastOverlayKey = '';  // 位置追従のキャッシュをリセット
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
          latestPlayers = players;  // 配信サーバ /players へ
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
// オーバーレイをゲーム窓のクライアント矩形（物理px）に合わせる。
// 物理px→DIPはディスプレイのDPIを考慮して screen.screenToDipRect で変換。
let lastOverlayKey = '';
ipcMain.on('set-overlay-bounds', (_e, rect) => {
  if (!overlayWin || !rect || !rect.width || !rect.height) return;
  const key = `${rect.left},${rect.top},${rect.width},${rect.height}`;
  if (key === lastOverlayKey) return;  // 変化が無ければ動かさない
  lastOverlayKey = key;
  try {
    const dip = screen.screenToDipRect(overlayWin, {
      x: rect.left, y: rect.top, width: rect.width, height: rect.height,
    });
    overlayWin.setBounds({
      x: Math.round(dip.x), y: Math.round(dip.y),
      width: Math.round(dip.width), height: Math.round(dip.height),
    });
  } catch (err) {
    console.error('[4v4Wars] オーバーレイ位置調整に失敗:', err.message);
  }
});

ipcMain.on('win-minimize', (e) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  if (w) w.minimize();
});
ipcMain.on('app-quit', () => { killWorker(); app.quit(); });

// ── 配信用HTTPサーバ（OBSブラウザソース用） ──
// OBSの「ブラウザソース」に http://127.0.0.1:17654/ を指定すると、画面上の透明
// ウィンドウとは別に、配信に確実に乗るオーバーレイを得られる（ゲームキャプチャでも映る）。
const STREAM_PORT = 17654;
const STREAM_STATIC = {
  '/': 'stream.html',
  '/stream.html': 'stream.html',
  '/css/in_game.css': 'css/in_game.css',
  '/js/stream.js': 'js/stream.js',
};
function streamMime(file) {
  if (file.endsWith('.html')) return 'text/html; charset=utf-8';
  if (file.endsWith('.css')) return 'text/css; charset=utf-8';
  if (file.endsWith('.js')) return 'application/javascript; charset=utf-8';
  return 'application/octet-stream';
}
function startStreamServer() {
  streamServer = http.createServer((req, res) => {
    const url = (req.url || '/').split('?')[0];
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Cache-Control', 'no-store');
    if (url === '/teams') {
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      res.end(JSON.stringify(readJson(teamsFile(), null)));
      return;
    }
    if (url === '/players') {
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      res.end(JSON.stringify(latestPlayers));
      return;
    }
    const file = STREAM_STATIC[url];
    if (file) {
      fs.readFile(path.join(ROOT, file), (err, buf) => {
        if (err) { res.statusCode = 404; res.end('not found'); return; }
        res.setHeader('Content-Type', streamMime(file));
        res.end(buf);
      });
      return;
    }
    res.statusCode = 404;
    res.end('not found');
  });
  streamServer.on('error', (e) => console.error('[4v4Wars] 配信サーバ起動失敗:', e.message));
  streamServer.listen(STREAM_PORT, '127.0.0.1', () => {
    console.log(`[4v4Wars] 配信オーバーレイ: http://127.0.0.1:${STREAM_PORT}/ （OBSブラウザソースに設定）`);
  });
}

// ── ライフサイクル ──
app.whenReady().then(() => {
  CONFIG_DIR = app.getPath('userData');
  createHomeWindow();
  startStreamServer();
  setInterval(pollLiveClient, 2000);
  pollLiveClient();
});

// 終了時はワーカー停止＋配信サーバのクローズ（プロセス終了でも閉じるが明示的に）
function shutdown() {
  killWorker();
  if (streamServer) { try { streamServer.close(); } catch (e) { /* ignore */ } streamServer = null; }
}
app.on('window-all-closed', () => { shutdown(); app.quit(); });
app.on('before-quit', () => shutdown());
