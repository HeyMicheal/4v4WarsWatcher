const MAX_MEMBERS = 4;
const STORAGE_KEY = '4v4wars_teams';

const state = {
  a: [],
  b: [],
};

function persist() {
  const data = {
    teamA: { name: document.getElementById('team-a-name').value.trim() || 'Team A', members: state.a },
    teamB: { name: document.getElementById('team-b-name').value.trim() || 'Team B', members: state.b },
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

function load() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;

  try {
    const data = JSON.parse(raw);
    if (data.teamA) {
      document.getElementById('team-a-name').value = data.teamA.name || 'Team A';
      state.a = data.teamA.members || [];
    }
    if (data.teamB) {
      document.getElementById('team-b-name').value = data.teamB.name || 'Team B';
      state.b = data.teamB.members || [];
    }
    renderMembers('a');
    renderMembers('b');
  } catch (e) {
    // 壊れたデータは無視
  }
}

function addMember(team) {
  const input = document.getElementById(`team-${team}-input`);
  const raw = input.value.trim();

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
  input.value = '';
  renderMembers(team);
  persist();
  showStatus('');
}

function removeMember(team, index) {
  state[team].splice(index, 1);
  renderMembers(team);
  persist();
}

function resetTeams() {
  state.a = [];
  state.b = [];
  document.getElementById('team-a-name').value = 'Team A';
  document.getElementById('team-b-name').value = 'Team B';
  renderMembers('a');
  renderMembers('b');
  persist();
  showStatus('リセットしました');
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

document.addEventListener('DOMContentLoaded', () => {
  load();

  ['a', 'b'].forEach((team) => {
    document.getElementById(`team-${team}-input`).addEventListener('keydown', (e) => {
      if (e.key === 'Enter') addMember(team);
    });
    // チーム名変更も自動保存
    document.getElementById(`team-${team}-name`).addEventListener('input', persist);
  });
});
