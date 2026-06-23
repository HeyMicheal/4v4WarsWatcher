const { app, BrowserWindow } = require('@overwolf/ow-electron');
const path = require('path');

const GAME_ID_TFT = 5426;

let homeWindow = null;
let inGameWindow = null;

function createHomeWindow() {
  homeWindow = new BrowserWindow({
    width: 780,
    height: 600,
    resizable: false,
    title: '4v4 Wars Watcher',
    icon: path.join(__dirname, '../../img/icons/icon_256.png'),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  homeWindow.loadFile(path.join(__dirname, '../home/index.html'));
  homeWindow.on('closed', () => { homeWindow = null; });
}

function createInGameWindow() {
  if (inGameWindow) return;

  inGameWindow = new BrowserWindow({
    width: 1920,
    height: 1080,
    transparent: true,
    frame: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  inGameWindow.loadFile(path.join(__dirname, '../in_game/index.html'));
  inGameWindow.setIgnoreMouseEvents(true);
  inGameWindow.on('closed', () => { inGameWindow = null; });
}

function closeInGameWindow() {
  if (inGameWindow) {
    inGameWindow.close();
    inGameWindow = null;
  }
}

// TFT起動・終了の検知（ow-electron GEPパッケージ）
app.on('ready', () => {
  createHomeWindow();

  // TODO: GEPパッケージのAPIが確定次第、ゲーム起動検知を実装
  // 暫定: homeウィンドウからのIPCでオーバーレイを制御できるよう準備
});

app.on('window-all-closed', () => {
  app.quit();
});
