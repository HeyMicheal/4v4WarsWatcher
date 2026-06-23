// TFTはLoLと同じ実行ファイルのため classId が 5426 / 21570 両方あり得る
const TFT_GAME_IDS = [5426, 21570];

let inGameWindow = null;

overwolf.extensions.onAppLaunchTriggered.addListener(openApp);

async function openApp() {
  openHomeWindow();
  const gameInfo = await getRunningGameInfo();
  if (gameInfo && isTFT(gameInfo.classId)) {
    openInGameWindow();
  }
}

overwolf.games.onGameInfoUpdated.addListener((event) => {
  if (!event.runningChanged) return;
  if (event.gameInfo && isTFT(event.gameInfo.classId)) {
    openInGameWindow();
  } else {
    closeInGameWindow();
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
  if (inGameWindow) {
    overwolf.windows.close(inGameWindow.id, () => {});
    inGameWindow = null;
  }
}

openApp();
