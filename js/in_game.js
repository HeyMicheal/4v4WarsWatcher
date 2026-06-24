// オーバーレイ表示ロジック
//   HP・チーム名・メンバー → Pythonワーカーの /stats (HTTP) から取得
//   生存/脱落          → Overwolf GEP (live_client_data.all_players の isDead)

// ワーカーのHTTP配信先（worker/config.json の http_port と合わせる）
const WORKER_URL = 'http://127.0.0.1:17653/stats';
const POLL_MS = 500;

const GEP_FEATURES = ['live_client_data'];

let workerStats = null;     // ワーカーから取得した最新の集計
let deadByName = {};        // プレイヤー名(小文字) -> 脱落しているか

// ── ワーカーからHPを取得 ──
async function pollWorker() {
  try {
    const res = await fetch(WORKER_URL, { cache: 'no-store' });
    workerStats = await res.json();
  } catch (e) {
    workerStats = null;  // ワーカー未起動など
  }
  render();
}

// ── GEP: all_players から脱落状態を更新 ──
function updateFromAllPlayers(raw) {
  try {
    const players = typeof raw === 'string' ? JSON.parse(raw) : raw;
    players.forEach((p) => {
      const name = (p.riotIdGameName || p.summonerName || '').split('#')[0].toLowerCase();
      if (!name) return;
      deadByName[name] = p.isDead === true;
    });
    render();
  } catch (e) {
    console.error('[4v4Wars] all_players のパースに失敗:', e);
  }
}

// ── 表示更新 ──
function render() {
  renderTeam('a', workerStats?.teamA);
  renderTeam('b', workerStats?.teamB);
}

function renderTeam(side, team) {
  const nameEl = document.getElementById(`team-${side}-name`);
  const hpEl = document.getElementById(`team-${side}-hp`);
  const aliveEl = document.getElementById(`team-${side}-alive`);
  const panel = document.getElementById(`team-${side}-panel`);

  if (!team) {
    // ワーカー未接続時は値を伏せる（チーム名は維持）
    hpEl.textContent = '--';
    aliveEl.textContent = '--';
    panel.classList.remove('eliminated');
    return;
  }

  nameEl.textContent = team.name || nameEl.textContent;
  hpEl.textContent = (team.totalHp ?? '--');
  if (team.color) panel.style.borderTopColor = team.color;  // チームカラーを枠上部に反映

  // 生存数: ワーカーが持つメンバーのうち、GEPで脱落していない人数
  const members = team.members || [];
  const alive = members.filter((m) => !deadByName[(m.name || '').toLowerCase()]).length;
  aliveEl.textContent = `${alive}/${members.length}`;
  panel.classList.toggle('eliminated', members.length > 0 && alive === 0);
}

// ── GEP イベント購読 ──
overwolf.games.events.onInfoUpdates2.addListener((event) => {
  if (event.feature === 'live_client_data' && event.info?.live_client_data?.all_players) {
    updateFromAllPlayers(event.info.live_client_data.all_players);
  }
});

// ── GEP getInfo ポーリング（生存の取りこぼし対策） ──
function fetchGepInfo() {
  overwolf.games.events.getInfo((info) => {
    if (!info?.res) return;
    if (info.res.live_client_data?.all_players) {
      updateFromAllPlayers(info.res.live_client_data.all_players);
    }
  });
}

function registerFeatures(retryCount = 0) {
  overwolf.games.events.setRequiredFeatures(GEP_FEATURES, (result) => {
    if (result.status === 'success') {
      fetchGepInfo();
      setInterval(fetchGepInfo, 3000);
    } else if (retryCount < 5) {
      setTimeout(() => registerFeatures(retryCount + 1), 2000);
    }
  });
}

// ── 初期化 ──
registerFeatures();
setInterval(pollWorker, POLL_MS);
pollWorker();
