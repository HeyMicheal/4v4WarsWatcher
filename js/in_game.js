// オーバーレイ表示ロジック
//   各プレイヤーの 位置・HP → Pythonワーカーの /stats (HTTP) から取得
//   チーム分け・色          → ホーム画面の設定（localStorage）
//   生存/脱落              → Overwolf GEP (live_client_data.all_players の isDead)
// ワーカーは「誰が・どのY位置で・何HPか」だけを返す。チームへの振り分け・
// 合計HP・マーカー色・生存数はすべてこのオーバーレイ側で組み立てる。

// ワーカーのHTTP配信先（worker/config.json の http_port と合わせる）
const WORKER_URL = 'http://127.0.0.1:17653/stats';
const POLL_MS = 500;

const GEP_FEATURES = ['live_client_data'];

// ホーム画面のチーム設定の保存キー（home.js と合わせる）
const TEAMS_KEY = '4v4wars_teams';

let workerStats = null;     // ワーカーから取得した最新のプレイヤー情報
let deadByName = {};        // プレイヤー名(小文字) -> 脱落しているか
let teams = loadTeams();    // {a:{name,members[],color}, b:{...}}

// ── ホーム画面のチーム設定を読み込む ──
function loadTeams() {
  const def = {
    a: { name: 'Team A', members: [], color: '#4a90d9', icon: null },
    b: { name: 'Team B', members: [], color: '#d9604a', icon: null },
  };
  try {
    const data = JSON.parse(localStorage.getItem(TEAMS_KEY));
    if (!data) return def;
    return {
      a: toSide(data.teamA, def.a),
      b: toSide(data.teamB, def.b),
    };
  } catch (e) {
    return def;
  }
}

// 保存形式 {name, members:[{name,tag}], color, icon} を {name, members:[{name}], color, icon} に正規化
// アイコンはチーム単位（メンバーごとではない）
function toSide(team, fallback) {
  if (!team) return fallback;
  return {
    name: team.name || fallback.name,
    members: (team.members || []).map((m) => (typeof m === 'string' ? { name: m } : { name: m.name })),
    color: team.color || fallback.color,
    icon: team.icon || null,
  };
}

// 名前(小文字) -> {side, color, icon} の対応表を作る（アイコンはチームのもの）
function buildNameMap() {
  const map = {};
  ['a', 'b'].forEach((side) => {
    teams[side].members.forEach((m) => {
      map[m.name.toLowerCase()] = { side, color: teams[side].color, icon: teams[side].icon };
    });
  });
  return map;
}

// チーム設定の読み込み状況をログに出す（生存数が出ない時の切り分け用）
function logTeams(when) {
  const names = (side) => teams[side].members.map((m) => m.name).join(',');
  console.log(`[4v4Wars] チーム設定(${when}): `
    + `A=${teams.a.name}[${names('a')}] B=${teams.b.name}[${names('b')}]`);
}
logTeams('起動時');

// ホーム画面で設定が変わったら追従する（別ウィンドウの storage イベント）
window.addEventListener('storage', (event) => {
  if (event.key === TEAMS_KEY) {
    teams = loadTeams();
    logTeams('更新');
    render();
  }
});

// ── ワーカーからプレイヤー情報を取得 ──
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
  const players = Array.isArray(workerStats?.players) ? workerStats.players : [];
  // 名前(小文字) -> {y, hp}
  const byName = {};
  players.forEach((p) => { byName[(p.name || '').toLowerCase()] = p; });

  renderTeam('a', byName);
  renderTeam('b', byName);
  renderMarkers(players);
}

// TFTプレイヤーリストの各キャラ肖像の右下フチに、チーム色のバッジを重ねる。
// 肖像は右端の丸いアイコン（中心 X≈1877, Y≈行中心-2, 1920x1080固定）。
// バッジ中心 = 肖像中心 + (22, 22) ≈ 右下45°のフチに半分重なる位置。
const BADGE_X = 1899;   // 1877 + 22
const BADGE_DY = 20;    // (行中心-2) + 22

function renderMarkers(players) {
  const container = document.getElementById('markers');
  container.innerHTML = '';
  const nameMap = buildNameMap();
  players.forEach((p) => {
    if (p.y == null) return;  // 位置未取得は描けない
    const info = nameMap[(p.name || '').toLowerCase()];
    if (!info) return;        // どちらのチームでもない名前は無視
    const badge = document.createElement('div');
    badge.className = 'team-marker';
    badge.style.left = `${BADGE_X}px`;
    badge.style.top = `${p.y + BADGE_DY}px`;
    if (info.icon) {
      // アイコンがあればマーカーをアイコンに置換（周囲のリングは白で統一）
      badge.classList.add('has-icon');
      badge.style.backgroundImage = `url("${info.icon}")`;
      badge.style.borderColor = '#fff';
    } else {
      // アイコン未設定は従来どおりチーム色の塗りバッジ
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

  nameEl.textContent = team.name;
  panel.style.borderTopColor = team.color;  // チームカラーを枠上部に反映

  // 合計HP: ワーカーが読めているメンバーのHPを合算。
  // 死亡プレイヤーは0扱い（死亡後に最終OCR値が加算され続けるのを防ぐ）。
  let totalHp = 0;
  team.members.forEach((m) => {
    const lower = m.name.toLowerCase();
    if (deadByName[lower]) return;  // 死亡者はHP=0
    const p = byName[lower];
    if (p && p.hp != null) totalHp += p.hp;
  });
  // ワーカー未接続時は値を伏せる（チーム名・色は維持）
  hpEl.textContent = workerStats ? totalHp : '--';

  // 生存数: メンバーのうちGEPで脱落していない人数
  const alive = team.members.filter((m) => !deadByName[m.name.toLowerCase()]).length;
  aliveEl.textContent = `${alive}/${team.members.length}`;
  panel.classList.toggle('eliminated', team.members.length > 0 && alive === 0);
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
