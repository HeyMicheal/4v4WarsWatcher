const STORAGE_KEY = '4v4wars_teams';
const GEP_FEATURES = ['roster', 'live_client_data', 'match_info'];

let teamConfig = null;

// プレイヤーキャッシュ: { "名前(小文字)": { isDead: boolean } }
let playerCache = {};

// ── チーム設定 ──
function loadTeamConfig() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  try {
    teamConfig = JSON.parse(raw);
    applyTeamNames();
    render();
  } catch (e) {
    console.error('[4v4Wars] チーム設定の読み込みに失敗:', e);
  }
}

function applyTeamNames() {
  if (!teamConfig) return;
  document.getElementById('team-a-name').textContent = teamConfig.teamA?.name || 'Team A';
  document.getElementById('team-b-name').textContent = teamConfig.teamB?.name || 'Team B';
}

// ── 表示更新 ──
function calcAlive(members) {
  return members.filter((m) => {
    const p = playerCache[m.name.toLowerCase()];
    return p && !p.isDead;
  }).length;
}

function render() {
  if (!teamConfig) return;

  const membersA = teamConfig.teamA?.members || [];
  const membersB = teamConfig.teamB?.members || [];
  const aliveA = calcAlive(membersA);
  const aliveB = calcAlive(membersB);

  document.getElementById('team-a-alive').textContent = `${aliveA}/${membersA.length}`;
  document.getElementById('team-b-alive').textContent = `${aliveB}/${membersB.length}`;

  document.getElementById('team-a-panel').classList.toggle('eliminated', membersA.length > 0 && aliveA === 0);
  document.getElementById('team-b-panel').classList.toggle('eliminated', membersB.length > 0 && aliveB === 0);
}

// ── live_client_data.all_players から isDead を更新 ──
function updateFromAllPlayers(raw) {
  try {
    const players = typeof raw === 'string' ? JSON.parse(raw) : raw;
    players.forEach((p) => {
      const name = (p.riotIdGameName || p.summonerName || '').split('#')[0].toLowerCase();
      if (!name) return;
      if (!playerCache[name]) playerCache[name] = { isDead: false };
      playerCache[name].isDead = p.isDead === true;
    });
    render();
  } catch (e) {
    console.error('[4v4Wars] all_players のパースに失敗:', e);
  }
}

// ── match_info.round_type からステージを更新 ──
function updateStage(raw) {
  try {
    const rt = typeof raw === 'string' ? JSON.parse(raw) : raw;
    const stage = rt?.stage || '--';
    document.getElementById('stage-value').textContent = stage;
  } catch (e) {
    // ステージ表示失敗は無視
  }
}

// ── GEP イベント購読 ──
overwolf.games.events.onInfoUpdates2.addListener((event) => {
  if (event.feature === 'live_client_data' && event.info?.live_client_data?.all_players) {
    updateFromAllPlayers(event.info.live_client_data.all_players);
  }
  if (event.feature === 'match_info' && event.info?.match_info?.round_type) {
    updateStage(event.info.match_info.round_type);
  }
});

// ── getInfo ポーリング（5秒ごとに最新状態を取得） ──
let pollInterval = null;

function fetchAndApplyInfo() {
  overwolf.games.events.getInfo((info) => {
    if (!info?.res) return;

    if (info.res.live_client_data?.all_players) {
      updateFromAllPlayers(info.res.live_client_data.all_players);
    }
    if (info.res.match_info?.round_type) {
      updateStage(info.res.match_info.round_type);
    }
  });
}

function startPolling() {
  fetchAndApplyInfo();
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(fetchAndApplyInfo, 5000);
}

// ── GEP フィーチャー登録 ──
function registerFeatures(retryCount = 0) {
  overwolf.games.events.setRequiredFeatures(GEP_FEATURES, (result) => {
    if (result.status === 'success') {
      startPolling();
    } else if (retryCount < 5) {
      setTimeout(() => registerFeatures(retryCount + 1), 2000);
    }
  });
}

// ── home 画面の設定変更を即時反映 ──
window.addEventListener('storage', (event) => {
  if (event.key === STORAGE_KEY) {
    loadTeamConfig();
  }
});

// ── 初期化 ──
loadTeamConfig();
registerFeatures();
