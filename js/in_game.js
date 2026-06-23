const STORAGE_KEY = '4v4wars_teams';
const GEP_FEATURES = ['roster', 'live_client_data', 'match_info'];

let teamConfig = null;

// プレイヤーキャッシュ: { "名前(小文字)": { health: number|null, isDead: boolean } }
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
function calcStats(members) {
  let totalHp = 0;
  let alive = 0;
  let hasHp = false;

  members.forEach((member) => {
    const p = playerCache[member.name.toLowerCase()];
    if (!p) return;
    if (p.health !== null && p.health !== undefined) {
      totalHp += p.health;
      hasHp = true;
    }
    if (!p.isDead) alive++;
  });

  return { totalHp, alive, hasHp };
}

function render() {
  if (!teamConfig) return;

  const membersA = teamConfig.teamA?.members || [];
  const membersB = teamConfig.teamB?.members || [];
  const statsA = calcStats(membersA);
  const statsB = calcStats(membersB);

  document.getElementById('team-a-hp').textContent    = statsA.hasHp ? statsA.totalHp : '--';
  document.getElementById('team-b-hp').textContent    = statsB.hasHp ? statsB.totalHp : '--';
  document.getElementById('team-a-alive').textContent = `${statsA.alive}/${membersA.length}`;
  document.getElementById('team-b-alive').textContent = `${statsB.alive}/${membersB.length}`;

  const elimA = membersA.length > 0 && statsA.alive === 0;
  const elimB = membersB.length > 0 && statsB.alive === 0;
  document.getElementById('team-a-panel').classList.toggle('eliminated', elimA);
  document.getElementById('team-b-panel').classList.toggle('eliminated', elimB);
}

// ── live_client_data.all_players から isDead を更新 ──
// プレイヤー名は riotIdGameName (空の場合は summonerName の # 前) を使用
function updateFromAllPlayers(raw) {
  try {
    const players = typeof raw === 'string' ? JSON.parse(raw) : raw;
    players.forEach((p) => {
      const name = (p.riotIdGameName || p.summonerName || '').split('#')[0].toLowerCase();
      if (!name) return;
      if (!playerCache[name]) playerCache[name] = { health: null, isDead: false };
      playerCache[name].isDead = p.isDead === true;
    });
    render();
  } catch (e) {
    console.error('[4v4Wars] all_players のパースに失敗:', e);
  }
}

// ── roster.player_status から health を更新 ──
function updateFromRoster(raw) {
  try {
    const data = typeof raw === 'string' ? JSON.parse(raw) : raw;

    if (Array.isArray(data)) {
      // 配列形式: [{ name/summoner_name, health }]
      data.forEach((p) => {
        const name = (p.summoner_name || p.name || '').toLowerCase();
        if (!name) return;
        if (!playerCache[name]) playerCache[name] = { health: null, isDead: false };
        playerCache[name].health = Number(p.health) || 0;
        playerCache[name].isDead = playerCache[name].health === 0;
      });
    } else {
      // オブジェクト形式: { "name": { health, ... } }
      Object.entries(data).forEach(([name, info]) => {
        const key = name.toLowerCase();
        if (!playerCache[key]) playerCache[key] = { health: null, isDead: false };
        playerCache[key].health = Number(info.health) || 0;
        playerCache[key].isDead = playerCache[key].health === 0;
      });
    }
    render();
  } catch (e) {
    console.error('[4v4Wars] player_status のパースに失敗:', e);
  }
}

// ── GEP イベント購読 ──
overwolf.games.events.onInfoUpdates2.addListener((event) => {
  console.log('[4v4Wars] onInfoUpdates2 feature:', event.feature);

  if (event.feature === 'live_client_data' && event.info?.live_client_data?.all_players) {
    updateFromAllPlayers(event.info.live_client_data.all_players);
  }

  if (event.feature === 'roster' && event.info?.roster?.player_status) {
    updateFromRoster(event.info.roster.player_status);
  }
});

// ── GEP フィーチャー登録 → 初期状態を即時取得 ──
function registerFeatures(retryCount = 0) {
  overwolf.games.events.setRequiredFeatures(GEP_FEATURES, (result) => {
    console.log('[4v4Wars] setRequiredFeatures:', JSON.stringify(result));
    if (result.status === 'success') {
      overwolf.games.events.getInfo((info) => {
        console.log('[4v4Wars] getInfo res keys:', Object.keys(info?.res || {}));

        // all_players で生存状態を初期化（必ず存在する）
        if (info?.res?.live_client_data?.all_players) {
          updateFromAllPlayers(info.res.live_client_data.all_players);
        }
        // roster があれば HP も初期化
        if (info?.res?.roster?.player_status) {
          updateFromRoster(info.res.roster.player_status);
        }
      });
    } else if (retryCount < 5) {
      setTimeout(() => registerFeatures(retryCount + 1), 2000);
    } else {
      console.error('[4v4Wars] GEP registration 失敗:', result);
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
