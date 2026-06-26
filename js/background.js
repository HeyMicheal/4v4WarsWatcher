// TFTはLoLと同じ実行ファイルのため classId が 5426 / 21570 両方あり得る
const TFT_GAME_IDS = [5426, 21570];

let inGameWindow = null;
let gameRunning = false;  // TFTが実行中か

overwolf.extensions.onAppLaunchTriggered.addListener(openApp);

async function openApp() {
  openHomeWindow();
  const gameInfo = await getRunningGameInfo();
  if (gameInfo && isTFT(gameInfo.classId)) {
    gameRunning = true;
    openInGameWindow();
  }
}

overwolf.games.onGameInfoUpdated.addListener((event) => {
  if (!event.runningChanged) return;
  if (event.gameInfo && isTFT(event.gameInfo.classId)) {
    gameRunning = true;
    openInGameWindow();
  } else {
    gameRunning = false;
    closeInGameWindow();
  }
});

// ホーム画面でワーカー設定が保存されたら、ゲーム実行中ならワーカーを起動する
// （オーバーレイ起動後にパスを設定した場合に対応）。入力が落ち着くまで待つ。
let workerSettingsTimer = null;
window.addEventListener('storage', (event) => {
  if (event.key === WORKER_SETTINGS_KEY && gameRunning) {
    clearTimeout(workerSettingsTimer);
    workerSettingsTimer = setTimeout(launchWorker, 1500);
  }
});

function isTFT(classId) {
  return TFT_GAME_IDS.includes(classId);
}

function getRunningGameInfo() {
  return new Promise((resolve) => {
    overwolf.games.getRunningGameInfo((result) => {
      resolve(result);
    });
  });
}

function openHomeWindow() {
  overwolf.windows.obtainDeclaredWindow('home', (result) => {
    if (result.status === 'success') {
      overwolf.windows.restore(result.window.id, () => {});
    }
  });
}

function openInGameWindow() {
  launchWorker();  // オーバーレイ表示と同時にOCRワーカーを起動
  pushRoster();    // ホーム画面のロスターをワーカーへ送る（config.jsonより優先させる）
  overwolf.windows.obtainDeclaredWindow('in_game', (result) => {
    if (result.status !== 'success') return;
    inGameWindow = result.window;
    overwolf.windows.restore(inGameWindow.id, () => {
      // オーバーレイを画面左上(0,0)に固定
      overwolf.windows.changePosition(inGameWindow.id, 0, 0, () => {});
    });
  });
}

function closeInGameWindow() {
  killWorker();  // オーバーレイ終了でワーカーも停止
  if (inGameWindow) {
    overwolf.windows.close(inGameWindow.id, () => {});
    inGameWindow = null;
  }
}

// ── ホーム画面のロスターをワーカーへ送る ──
// ワーカーは起動時に古い config.json を読むため、ホーム画面の入力（localStorage）を
// 起動のたびに送り直して上書きさせる。ワーカーのHTTPサーバが立ち上がるまで
// 数秒かかるので、成功するまでリトライする。
const TEAMS_KEY = '4v4wars_teams';
const WORKER_CONFIG_URL = 'http://127.0.0.1:17653/config';

function rosterNames() {
  try {
    const data = JSON.parse(localStorage.getItem(TEAMS_KEY));
    const names = [];
    ['teamA', 'teamB'].forEach((k) => {
      (data?.[k]?.members || []).forEach((m) => {
        const n = typeof m === 'string' ? m : m.name;
        if (n) names.push(n);
      });
    });
    return names;
  } catch (e) {
    return [];
  }
}

function pushRoster(attempt = 0) {
  const names = rosterNames();
  if (!names.length) return;  // 未設定なら送らない（config.jsonのまま）
  fetch(WORKER_CONFIG_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ names }),
  }).then((r) => {
    if (!r.ok) throw new Error('status ' + r.status);
    console.log('[4v4Wars] ロスターをワーカーへ送信:', JSON.stringify(names));
  }).catch(() => {
    // ワーカー起動直後はまだHTTPサーバが立っていない。最大40秒リトライ
    if (attempt < 20) setTimeout(() => pushRoster(attempt + 1), 2000);
  });
}

// ── OCRワーカーの起動/停止（C#プラグイン経由） ──
const WORKER_SETTINGS_KEY = '4v4wars_worker';

// プラグインのインスタンスをキャッシュして使い回す。
// 起動と停止で別インスタンスになると、Killが効かず（別インスタンスは
// 起動したプロセスを持たない）ワーカーが残るため、必ず同じものを使う。
let _launcherPromise = null;
function getLauncher() {
  if (!_launcherPromise) {
    _launcherPromise = new Promise((resolve) => {
      overwolf.extensions.current.getExtraObject('worker-launcher', (result) => {
        resolve(result && result.status === 'success' ? result.object : null);
      });
    });
  }
  return _launcherPromise;
}

async function launchWorker() {
  const raw = localStorage.getItem(WORKER_SETTINGS_KEY);
  if (!raw) {
    console.log('[4v4Wars] ワーカーパス未設定（ホーム画面で設定してください）');
    return;
  }
  let cfg;
  try { cfg = JSON.parse(raw); } catch (e) { return; }
  if (!cfg.workerDir) return;

  const launcher = await getLauncher();
  if (!launcher) {
    console.error('[4v4Wars] worker-launcher プラグインの取得に失敗');
    return;
  }
  const python = cfg.pythonCmd || 'pythonw';
  const dir = cfg.workerDir.replace(/[\\/]+$/, '');
  const script = dir + '\\worker.py';
  const tesseract = cfg.tesseractCmd || '';
  // Overwolfのブリッジは非ASCII(日本語パス)でエラーになるためBase64で渡す
  launcher.Launch(
    b64utf8(python), b64utf8('"' + script + '"'), b64utf8(dir), b64utf8(tesseract),
    (r) => { console.log('[4v4Wars] worker launch:', JSON.stringify(r)); }
  );
}

// 文字列をUTF-8 Base64にエンコードする
function b64utf8(s) {
  return btoa(unescape(encodeURIComponent(s)));
}

function killWorker() {
  return new Promise(async (resolve) => {
    const launcher = await getLauncher();
    if (!launcher) return resolve();
    launcher.Kill(() => resolve());
  });
}

// ── アプリ全終了（ホーム画面の×から呼ばれる） ──
const QUIT_KEY = '4v4wars_quit';

window.addEventListener('storage', (event) => {
  if (event.key === QUIT_KEY) quitApp();
});

async function quitApp() {
  await killWorker();  // ワーカーを止めてから
  ['in_game', 'home', 'background'].forEach((name) => {
    overwolf.windows.obtainDeclaredWindow(name, (r) => {
      if (r.status === 'success') overwolf.windows.close(r.window.id, () => {});
    });
  });
}

openApp();
