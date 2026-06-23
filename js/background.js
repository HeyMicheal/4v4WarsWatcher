const GAME_ID_TFT = 21570;

let inGameWindow = null;

overwolf.extensions.onAppLaunchTriggered.addListener(openApp);

async function openApp() {
  openHomeWindow();
  const gameInfo = await getRunningGameInfo();
  if (gameInfo && gameInfo.classId === GAME_ID_TFT) {
    openInGameWindow();
  }
}

function openHomeWindow() {
  overwolf.windows.obtainDeclaredWindow("home", (result) => {
    if (result.status === "success") {
      overwolf.windows.restore(result.window.id, () => {});
    }
  });
}

overwolf.games.onGameInfoUpdated.addListener((event) => {
  if (event.runningChanged) {
    if (event.gameInfo && event.gameInfo.classId === GAME_ID_TFT) {
      openInGameWindow();
    } else {
      closeInGameWindow();
    }
  }
});

function getRunningGameInfo() {
  return new Promise((resolve) => {
    overwolf.games.getRunningGameInfo((result) => {
      resolve(result);
    });
  });
}

function openInGameWindow() {
  overwolf.windows.obtainDeclaredWindow("in_game", (result) => {
    if (result.status === "success") {
      inGameWindow = result.window;
      overwolf.windows.restore(inGameWindow.id, () => {});
    }
  });
}

function closeInGameWindow() {
  if (inGameWindow) {
    overwolf.windows.close(inGameWindow.id, () => {});
    inGameWindow = null;
  }
}

openApp();
