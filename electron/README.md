# 4v4WarsWatcher — Electron版（Overwolf非依存）

Overwolf の代わりに **Electron** でオーバーレイを出し、**ライブクライアントデータAPI
(127.0.0.1:2999)** で生存情報を取る構成です。OCRワーカー（Python）はそのまま流用します。

## 構成
- `electron/main.js` … メインプロセス
  - home / overlay の2ウィンドウ管理
  - Pythonワーカーを child_process で起動/停止（旧 C#プラグイン）
  - ライブクライアントデータをポーリングし、試合中はオーバーレイ＋ワーカーを起動（旧 GEP）
  - チーム設定/ワーカー設定をファイルで保持（旧 localStorage 共有）
- `electron/preload.js` … レンダラーへ `window.host` を公開
- `home.html` / `in_game.html` … UIはそのまま流用（JSは host 経由に変更済み）
- `worker/` … OCRワーカー（変更なし。Overwolf非依存）

## セットアップ（Windows）
```
cd 4v4WarsWatcher
npm install        # electron を取得
npm start          # アプリ起動（ホーム画面が開く）
```

## 使い方
1. 歯車 ⚙ → ワーカーフォルダ等を設定（例: `D:\...\4v4WarsWatcher\worker`）
2. チーム名・メンバー・色・アイコンを設定
3. TFTを観戦開始 → ライブクライアントデータを検知して、自動でオーバーレイ表示＋
   ワーカー起動
4. 名前が読めない場合はホームの「プレイヤー対応」/「位置から登録」で手動対応

## 注意
- オーバーレイは最前面・クリックスルーの透明ウィンドウ。**ボーダーレス/ウィンドウ表示**の
  ゲーム上に重なる（真の排他的フルスクリーンには重ねられない）。
- 解像度 1920x1080 前提（マーカー座標が固定）。
- 旧Overwolf用ファイル（`manifest.json` / `background.*` / `plugin/` / `plugins/`）は
  このブランチでは未使用。
