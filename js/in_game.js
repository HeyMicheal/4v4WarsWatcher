// チームA / Bのプレイヤー名（後でゲームイベントから動的に取得する想定）
const teamA = [];
const teamB = [];

function updateHpDisplay(teamATotal, teamBTotal) {
  document.getElementById("team-a-hp").textContent = teamATotal ?? "--";
  document.getElementById("team-b-hp").textContent = teamBTotal ?? "--";
}

// GEP（Game Events Provider）からのイベント購読
overwolf.games.events.onInfoUpdates2.addListener((event) => {
  // TODO: TFTのinfo_updatesからプレイヤーHPを取得してチーム別に集計
  console.log("info update:", event);
});

overwolf.games.events.onNewEvents.addListener((event) => {
  // TODO: TFTのゲームイベント（ダメージ・死亡等）を処理
  console.log("new event:", event);
});

// GEPの登録
overwolf.games.events.setRequiredFeatures(
  ["scene", "board", "player", "active_player"],
  (result) => {
    if (result.status === "success") {
      console.log("GEP features registered:", result);
    } else {
      console.error("GEP registration failed:", result);
    }
  }
);
