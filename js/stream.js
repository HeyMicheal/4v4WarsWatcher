// 配信用オーバーレイ（OBSブラウザソース）。Electronのhostに依存せず、
// すべてHTTPで取得する：
//   HP・位置  → Pythonワーカー /stats (127.0.0.1:17653, CORS許可)
//   チーム設定 → このページを配信する main の /teams
//   生存/脱落 → main の /players（main がライブクライアントデータをポーリング）
// ブラウザソースは1920x1080固定。ゲームキャプチャ(1920x1080)に重ねれば座標が一致する。

const WORKER_STATS_URL = 'http://127.0.0.1:17653/stats';
const TEAMS_URL = '/teams';      // main が配信（同一オリジン）
const PLAYERS_URL = '/players';  // main が配信（同一オリジン）

const STATS_MS = 500;
const PLAYERS_MS = 1000;
const TEAMS_MS = 2000;

let workerStats = null;
let deadByName = {};
let teams = defaultTeams();

function defaultTeams() {
  return {
    a: { name: 'Team A', members: [], color: '#4a90d9', icon: null },
    b: { name: 'Team B', members: [], color: '#d9604a', icon: null },
  };
}

function toSide(team, fallback) {
  if (!team) return fallback;
  return {
    name: team.name || fallback.name,
    members: (team.members || []).map((m) => (typeof m === 'string' ? { name: m } : { name: m.name })),
    color: team.color || fallback.color,
    icon: team.icon || null,
  };
}

function applyTeams(data) {
  const def = defaultTeams();
  teams = data ? { a: toSide(data.teamA, def.a), b: toSide(data.teamB, def.b) } : def;
  render();
}

function buildNameMap() {
  const map = {};
  ['a', 'b'].forEach((side) => {
    teams[side].members.forEach((m) => {
      map[m.name.toLowerCase()] = { side, color: teams[side].color, icon: teams[side].icon };
    });
  });
  return map;
}

// ── 取得 ──
async function pollWorker() {
  try {
    const res = await fetch(WORKER_STATS_URL, { cache: 'no-store' });
    workerStats = await res.json();
  } catch (e) {
    workerStats = null;
  }
  render();
}

async function pollPlayers() {
  try {
    const res = await fetch(PLAYERS_URL, { cache: 'no-store' });
    updateFromAllPlayers(await res.json());
  } catch (e) { /* 試合外など */ }
}

async function pollTeams() {
  try {
    const res = await fetch(TEAMS_URL, { cache: 'no-store' });
    applyTeams(await res.json());
  } catch (e) { /* main未起動など */ }
}

function updateFromAllPlayers(players) {
  if (!Array.isArray(players)) return;
  players.forEach((p) => {
    const raw = p.riotIdGameName || p.riotId || p.summonerName || '';
    const name = raw.split('#')[0].toLowerCase();
    if (!name) return;
    deadByName[name] = p.isDead === true;
  });
  render();
}

// ── 描画（in_game.js と同じロジック。ウィンドウ追従は無し＝1920x1080固定） ──
function render() {
  const players = Array.isArray(workerStats?.players) ? workerStats.players : [];
  const byName = {};
  players.forEach((p) => { byName[(p.name || '').toLowerCase()] = p; });
  renderTeam('a', byName);
  renderTeam('b', byName);
  renderMarkers(players);
}

const BADGE_X = 1899;
const BADGE_DY = 20;

function renderMarkers(players) {
  const container = document.getElementById('markers');
  container.innerHTML = '';
  const nameMap = buildNameMap();
  players.forEach((p) => {
    if (p.y == null) return;
    const info = nameMap[(p.name || '').toLowerCase()];
    if (!info) return;
    const badge = document.createElement('div');
    badge.className = 'team-marker';
    badge.style.left = `${BADGE_X}px`;
    badge.style.top = `${p.y + BADGE_DY}px`;
    if (info.icon) {
      badge.classList.add('has-icon');  // アイコン置換（リングなし）
      badge.style.backgroundImage = `url("${info.icon}")`;
    } else {
      badge.style.backgroundColor = info.color || '#fff';
    }
    container.appendChild(badge);
  });
}

function renderTeam(side, byName) {
  const team = teams[side];
  const nameEl = document.getElementById(`team-${side}-name`);
  const hpEl = document.getElementById(`team-${side}-hp`);
  const aliveEl = document.getElementById(`team-${side}-alive`);
  const panel = document.getElementById(`team-${side}-panel`);
  const iconEl = document.getElementById(`team-${side}-icon`);

  nameEl.textContent = team.name;
  panel.style.borderTopColor = team.color;

  // HPの左にチームアイコンを表示（未設定なら隠す）
  if (iconEl) {
    if (team.icon) { iconEl.src = team.icon; iconEl.style.display = ''; }
    else { iconEl.removeAttribute('src'); iconEl.style.display = 'none'; }
  }

  // 合計HP（死亡者は0扱い）
  let totalHp = 0;
  team.members.forEach((m) => {
    const lower = m.name.toLowerCase();
    if (deadByName[lower]) return;
    const p = byName[lower];
    if (p && p.hp != null) totalHp += p.hp;
  });
  hpEl.textContent = workerStats ? totalHp : '--';

  const alive = team.members.filter((m) => !deadByName[m.name.toLowerCase()]).length;
  aliveEl.textContent = `${alive}/${team.members.length}`;
  panel.classList.toggle('eliminated', team.members.length > 0 && alive === 0);
}

// ── 初期化 ──
pollTeams();
pollWorker();
pollPlayers();
setInterval(pollWorker, STATS_MS);
setInterval(pollPlayers, PLAYERS_MS);
setInterval(pollTeams, TEAMS_MS);
