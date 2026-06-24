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

// ── OCRワーカーの起動/停止（C#プラグイン経由） ──
const WORKER_SETTINGS_KEY = '4v4wars_worker';

function getExtraObject(name) {
  return new Promise((resolve) => {
    overwolf.extensions.current.getExtraObject(name, (result) => {
      resolve(result && result.status === 'success' ? result.object : null);
    });
  });
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

  const launcher = await getExtraObject('worker-launcher');
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
    const launcher = await getExtraObject('worker-launcher');
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
