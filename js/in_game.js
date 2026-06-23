const STORAGE_KEY = '4v4wars_teams';
const GEP_FEATURES = ['roster', 'match_info'];

let teamConfig = null;

// ── チーム設定をhomeウィンドウのlocalStorageから読み込む ──
function loadTeamConfig() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  try {
    teamConfig = JSON.parse(raw);
    applyTeamNames();
  } catch (e) {
    console.error('チーム設定の読み込みに失敗:', e);
  }
}

function applyTeamNames() {
  if (!teamConfig) return;
  document.getElementById('team-a-name').textContent = teamConfig.teamA?.name || 'Team A';
  document.getElementById('team-b-name').textContent = teamConfig.teamB?.name || 'Team B';
}

// ── player_status を正規化して { name, health } の配列に変換 ──
// GEPは { "プレイヤー名": { health, ... } } または [{ name, health, ... }] の両形式を想定
function normalizePlayers(raw) {
  if (Array.isArray(raw)) {
    return raw.map((p) => ({
      name: p.summoner_name || p.name || '',
      health: Number(p.health) || 0,
    }));
  }
  if (typeof raw === 'object' && raw !== null) {
    return Object.entries(raw).map(([name, data]) => ({
      name,
      health: Number(data.health) || 0,
    }));
  }
  return [];
}

// ── プレイヤーをチームに照合してHP・生存数を計算 ──
function calcTeamStats(members, players) {
  let totalHp = 0;
  let alive = 0;

  members.forEach((member) => {
    const matched = players.find(
      (p) => p.name.toLowerCase() === member.name.toLowerCase()
    );
    if (matched) {
      totalHp += matched.health;
      if (matched.health > 0) alive++;
    }
  });

  return { totalHp, alive };
}

function updateDisplay(players) {
  if (!teamConfig || players.length === 0) return;

  const statsA = calcTeamStats(teamConfig.teamA?.members || [], players);
  const statsB = calcTeamStats(teamConfig.teamB?.members || [], players);

  document.getElementById('team-a-hp').textContent = statsA.totalHp;
  document.getElementById('team-b-hp').textContent = statsB.totalHp;
  document.getElementById('team-a-alive').textContent =
    `${statsA.alive}/${teamConfig.teamA?.members?.length ?? 0}`;
  document.getElementById('team-b-alive').textContent =
    `${statsB.alive}/${teamConfig.teamB?.members?.length ?? 0}`;

  document.getElementById('team-a-panel').classList.toggle('eliminated', statsA.alive === 0);
  document.getElementById('team-b-panel').classList.toggle('eliminated', statsB.alive === 0);
}

function parseAndDisplay(playerStatusRaw) {
  try {
    const parsed = typeof playerStatusRaw === 'string'
      ? JSON.parse(playerStatusRaw)
      : playerStatusRaw;
    console.log('[4v4Wars] player_status raw:', JSON.stringify(parsed));
    const players = normalizePlayers(parsed);
    updateDisplay(players);
  } catch (e) {
    console.error('[4v4Wars] player_status のパースに失敗:', e, playerStatusRaw);
  }
}

// ── GEP イベント購読 ──
overwolf.games.events.onInfoUpdates2.addListener((event) => {
  console.log('[4v4Wars] onInfoUpdates2:', JSON.stringify(event));
  if (event.feature === 'roster' && event.info?.roster?.player_status) {
    parseAndDisplay(event.info.roster.player_status);
  }
});

// ── GEPフィーチャー登録 → 登録成功後に現在の状態を即時取得 ──
function registerFeatures(retryCount = 0) {
  overwolf.games.events.setRequiredFeatures(GEP_FEATURES, (result) => {
    console.log('[4v4Wars] setRequiredFeatures result:', JSON.stringify(result));
    if (result.status === 'success') {
      // 登録成功後、現在のゲーム状態をポーリングして初期値を取得
      overwolf.games.events.getInfo((infoResult) => {
        console.log('[4v4Wars] getInfo result:', JSON.stringify(infoResult));
        const playerStatus = infoResult?.res?.roster?.player_status;
        if (playerStatus) {
          parseAndDisplay(playerStatus);
        }
      });
    } else if (retryCount < 5) {
      setTimeout(() => registerFeatures(retryCount + 1), 2000);
    } else {
      console.error('[4v4Wars] GEP registration failed after retries:', result);
    }
  });
}

// ── homeウィンドウでの設定変更をリアルタイムで反映 ──
window.addEventListener('storage', (event) => {
  if (event.key === STORAGE_KEY) {
    loadTeamConfig();
  }
});

// ── 初期化 ──
loadTeamConfig();
registerFeatures();
