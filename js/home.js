const MAX_MEMBERS = 4;
const STORAGE_KEY = '4v4wars_teams';

const state = {
  a: [],
  b: [],
};

function getVal(id) {
  return document.getElementById(id).value.replace(/\n/g, '').trim();
}

function setVal(id, value) {
  document.getElementById(id).value = value;
}

// Pythonワーカーの設定更新先（worker/config.json の http_port と合わせる）
const WORKER_CONFIG_URL = 'http://127.0.0.1:17653/config';

// ワーカー起動設定（フォルダ・Pythonコマンド）の保存キー
const WORKER_SETTINGS_KEY = '4v4wars_worker';

function saveWorkerSettings() {
  const data = {
    workerDir: document.getElementById('worker-dir').value.trim(),
    pythonCmd: document.getElementById('worker-python').value.trim() || 'pythonw',
    tesseractCmd: document.getElementById('worker-tesseract').value.trim(),
  };
  localStorage.setItem(WORKER_SETTINGS_KEY, JSON.stringify(data));
}

function loadWorkerSettings() {
  const raw = localStorage.getItem(WORKER_SETTINGS_KEY);
  if (!raw) return;
  try {
    const data = JSON.parse(raw);
    document.getElementById('worker-dir').value = data.workerDir || '';
    document.getElementById('worker-python').value = data.pythonCmd || '';
    document.getElementById('worker-tesseract').value = data.tesseractCmd || '';
  } catch (e) {
    // 壊れたデータは無視
  }
}

function persist() {
  const data = {
    teamA: { name: getVal('team-a-name') || 'Team A', members: state.a, color: getColor('a') },
    teamB: { name: getVal('team-b-name') || 'Team B', members: state.b, color: getColor('b') },
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  sendToWorker(data);
}

function getColor(team) {
  return document.getElementById(`team-${team}-color`).value;
}

// ワーカーへ名前リストだけ送る（チーム分け・色はOverwolf側が持つ）。
// 名前はRiotIDのゲーム名部分（#タグより前）。OCRが読むTFT表示名に合わせる。
function sendToWorker(data) {
  const names = [...data.teamA.members, ...data.teamB.members].map((m) => m.name);
  const payload = { names };
  fetch(WORKER_CONFIG_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).catch(() => {
    // ワーカー未起動などは無視（localStorageには保存済み）
  });
}

function load() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;

  try {
    const data = JSON.parse(raw);
    if (data.teamA) {
      setVal('team-a-name', data.teamA.name || 'Team A');
      state.a = data.teamA.members || [];
      if (data.teamA.color) document.getElementById('team-a-color').value = data.teamA.color;
    }
    if (data.teamB) {
      setVal('team-b-name', data.teamB.name || 'Team B');
      state.b = data.teamB.members || [];
      if (data.teamB.color) document.getElementById('team-b-color').value = data.teamB.color;
    }
    renderMembers('a');
    renderMembers('b');
  } catch (e) {
    // 壊れたデータは無視
  }
}

function addMember(team) {
  const raw = getVal(`team-${team}-input`);

  if (!raw) return;

  if (!raw.includes('#')) {
    showStatus('RiotIDは「名前#タグ」の形式で入力してください', true);
    return;
  }

  const [name, tag] = raw.split('#');
  if (!name || !tag) {
    showStatus('RiotIDの形式が正しくありません', true);
    return;
  }

  if (state[team].length >= MAX_MEMBERS) {
    showStatus(`メンバーは最大${MAX_MEMBERS}人までです`, true);
    return;
  }

  const isDuplicate = [...state.a, ...state.b].some(
    (m) => m.name.toLowerCase() === name.toLowerCase() && m.tag.toLowerCase() === tag.toLowerCase()
  );
  if (isDuplicate) {
    showStatus('そのRiotIDはすでに追加されています', true);
    return;
  }

  state[team].push({ name, tag });
  setVal(`team-${team}-input`, '');
  renderMembers(team);
  persist();
  showStatus('');
}

function removeMember(team, index) {
  state[team].splice(index, 1);
  renderMembers(team);
  persist();
}

function resetTeam(team) {
  state[team] = [];
  renderMembers(team);
  persist();
}

function renderMembers(team) {
  const list = document.getElementById(`team-${team}-members`);
  list.innerHTML = '';

  state[team].forEach((member, i) => {
    const li = document.createElement('li');
    li.className = 'member-item';
    li.innerHTML = `
      <span>
        <span class="member-name">${escapeHtml(member.name)}</span>
        <span class="member-tag">#${escapeHtml(member.tag)}</span>
      </span>
      <button class="btn-remove" onclick="removeMember('${team}', ${i})" title="削除">×</button>
    `;
    list.appendChild(li);
  });
}

function showStatus(msg, isError = false) {
  const el = document.getElementById('status-msg');
  el.textContent = msg;
  el.style.color = isError ? '#e05050' : '#60c060';
  if (msg && !isError) {
    setTimeout(() => { el.textContent = ''; }, 2000);
  }
}

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ウィンドウ操作
let currentWindowId = null;

overwolf.windows.getCurrentWindow((result) => {
  if (result.status === 'success') {
    currentWindowId = result.window.id;
  }
});

function minimizeWindow() {
  overwolf.windows.minimize(currentWindowId, () => {});
}

function closeWindow() {
  // background に全終了（ワーカー停止＋全ウィンドウクローズ）を依頼する
  localStorage.setItem('4v4wars_quit', String(Date.now()));
}

document.addEventListener('DOMContentLoaded', () => {
  load();
  loadWorkerSettings();

  // ワーカー起動設定の自動保存
  document.getElementById('worker-dir').addEventListener('input', saveWorkerSettings);
  document.getElementById('worker-python').addEventListener('input', saveWorkerSettings);
  document.getElementById('worker-tesseract').addEventListener('input', saveWorkerSettings);

  // タイトルバードラッグ
  document.getElementById('title-bar-drag').addEventListener('mousedown', () => {
    overwolf.windows.dragMove(currentWindowId);
  });

  ['a', 'b'].forEach((team) => {
    const input = document.getElementById(`team-${team}-input`);
    const nameEl = document.getElementById(`team-${team}-name`);

    // RiotID入力: Enterで追加（変換確定中は無視）
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        if (!e.isComposing) addMember(team);
      }
    });

    // チーム名: Enterで改行させない
    nameEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        nameEl.blur();
      }
    });

    // チーム名変更を自動保存
    nameEl.addEventListener('input', persist);

    // チームカラー変更を自動保存
    document.getElementById(`team-${team}-color`).addEventListener('input', persist);
  });
});
