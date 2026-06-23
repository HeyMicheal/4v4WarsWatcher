const MAX_MEMBERS = 4;

const state = {
  a: [],
  b: [],
};

function addMember(team) {
  const input = document.getElementById(`team-${team}-input`);
  const raw = input.value.trim();

  if (!raw) return;

  // RiotID形式 (名前#タグ) のバリデーション
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
  showStatus('');
}

function removeMember(team, index) {
  state[team].splice(index, 1);
  renderMembers(team);
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

function saveTeams() {
  const teamAName = document.getElementById('team-a-name').value.trim() || 'Team A';
  const teamBName = document.getElementById('team-b-name').value.trim() || 'Team B';

  const data = {
    teamA: { name: teamAName, members: state.a },
    teamB: { name: teamBName, members: state.b },
  };

  overwolf.extensions.current.getExtraObject('settings', (result) => {
    if (result.status === 'success') {
      result.object.set('teams', JSON.stringify(data), () => {
        showStatus('設定を保存しました！');
      });
    }
  });
}

function showStatus(msg, isError = false) {
  const el = document.getElementById('status-msg');
  el.textContent = msg;
  el.style.color = isError ? '#e05050' : '#60c060';
}

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Enterキーでも追加できるように
document.addEventListener('DOMContentLoaded', () => {
  ['a', 'b'].forEach((team) => {
    document.getElementById(`team-${team}-input`).addEventListener('keydown', (e) => {
      if (e.key === 'Enter') addMember(team);
    });
  });
});
