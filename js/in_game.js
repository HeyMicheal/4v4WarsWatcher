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

// ── プレイヤーをチームに照合してHP・生存数を計算 ──
function calcTeamStats(members, players) {
  let totalHp = 0;
  let alive = 0;

  members.forEach((member) => {
    const matched = players.find((p) => {
      const name = (p.summoner_name || p.name || '').toLowerCase();
      return name === member.name.toLowerCase();
    });
    if (matched) {
      const hp = Number(matched.health) || 0;
      totalHp += hp;
      if (hp > 0) alive++;
    }
  });

  return { totalHp, alive };
}

function updateDisplay(players) {
  if (!teamConfig) return;

  const statsA = calcTeamStats(teamConfig.teamA?.members || [], players);
  const statsB = calcTeamStats(teamConfig.teamB?.members || [], players);

  document.getElementById('team-a-hp').textContent = statsA.totalHp;
  document.getElementById('team-b-hp').textContent = statsB.totalHp;
  document.getElementById('team-a-alive').textContent =
    `${statsA.alive}/${teamConfig.teamA?.members?.length ?? 0}`;
  document.getElementById('team-b-alive').textContent =
    `${statsB.alive}/${teamConfig.teamB?.members?.length ?? 0}`;

  // 全滅チームをグレーアウト
  document.getElementById('team-a-panel').classList.toggle('eliminated', statsA.alive === 0);
  document.getElementById('team-b-panel').classList.toggle('eliminated', statsB.alive === 0);
}

// ── GEP イベント購読 ──
overwolf.games.events.onInfoUpdates2.addListener((event) => {
  if (event.feature === 'roster' && event.info?.roster?.player_status) {
    try {
      const players = JSON.parse(event.info.roster.player_status);
      updateDisplay(players);
    } catch (e) {
      console.error('player_status のパースに失敗:', e);
    }
  }
});

// GEPフィーチャー登録（リトライあり）
function registerFeatures(retryCount = 0) {
  overwolf.games.events.setRequiredFeatures(GEP_FEATURES, (result) => {
    if (result.status === 'success') {
      console.log('GEP features registered:', result);
    } else if (retryCount < 5) {
      setTimeout(() => registerFeatures(retryCount + 1), 2000);
    } else {
      console.error('GEP registration failed after retries:', result);
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
