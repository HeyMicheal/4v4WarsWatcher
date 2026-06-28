// レンダラー（home / overlay）に安全なAPIだけを公開する。
// Overwolf の overwolf.* / GEP / localStorage 共有を置き換える窓口。
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('host', {
  // チーム設定（旧 localStorage '4v4wars_teams' の置き換え。ファイル管理）
  getTeams: () => ipcRenderer.invoke('get-teams'),
  setTeams: (data) => ipcRenderer.invoke('set-teams', data),
  onTeamsChanged: (cb) => ipcRenderer.on('teams', (_e, data) => cb(data)),

  // ワーカー起動設定（旧 localStorage '4v4wars_worker'）
  getWorkerSettings: () => ipcRenderer.invoke('get-worker-settings'),
  setWorkerSettings: (data) => ipcRenderer.invoke('set-worker-settings', data),

  // ライブクライアントデータ（旧 Overwolf GEP の置き換え）。allPlayers配列を受ける
  onPlayers: (cb) => ipcRenderer.on('players', (_e, players) => cb(players)),
  // 試合中かどうかの変化通知（オーバーレイ表示制御の参考）
  onGameActive: (cb) => ipcRenderer.on('game-active', (_e, active) => cb(active)),

  // オーバーレイをゲーム窓の矩形に合わせる（{left,top,width,height} 物理px）
  setOverlayBounds: (rect) => ipcRenderer.send('set-overlay-bounds', rect),

  // ウィンドウ操作（旧 overwolf.windows.*）
  minimize: () => ipcRenderer.send('win-minimize'),
  quit: () => ipcRenderer.send('app-quit'),
});
