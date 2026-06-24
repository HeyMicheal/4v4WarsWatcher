# 4v4WarsWatcher OCRワーカー

TFTの画面からプレイヤー名とHPを読み取り、Overwolfオーバーレイに渡すPythonワーカー。

> **重要:** このワーカーは **Windows側のPython** で実行します（WSL内ではキャプチャできません）。

---

## 全体の流れ

```
[WGC] TFTウィンドウをキャプチャ（他アプリが重なってもOK）
   ↓
[切り出し] プレイヤーリスト領域（固定座標）
   ↓
[OCR] 名前=EasyOCR / HP=tesseract psm6
   ↓
[照合] 登録RiotIDとファジーマッチ
   ↓
[配信] localhost:3000 でJSON出力 → Overwolfがポーリング
```

---

## ステップ1: キャプチャ検証（今ここ）

TFTウィンドウが正しく画像として取得できるかを確認する。

### 準備（Windowsのコマンドプロンプト / PowerShell）

```cmd
cd worker
python -m pip install -r requirements.txt
```

### 手順

1. **TFTを起動**して観戦画面を表示しておく

2. **ウィンドウタイトルを確認**
   ```cmd
   python list_windows.py
   ```
   出力から `League of Legends (TM) Client` のような行を探す。
   違う文字列なら `capture_test.py` の `WINDOW_NAME` を書き換える。

3. **キャプチャ実行**
   ```cmd
   python capture_test.py
   ```

4. **結果確認**
   生成された `capture.png` を開く。
   - ✅ TFTの画面が写っていて、右側にプレイヤーリストが見える → 成功（ステップ2へ）
   - ❌ 真っ黒 → `WINDOW_NAME` が違うウィンドウを指している。手順2をやり直す

---

---

## ステップ2: OCRパイプライン（案Y: 毎回フルOCR・3秒間隔）

キャプチャ画像から名前・HPを読み取り、登録チームと照合してチーム集計する。

### 事前準備

1. **Tesseract本体をインストール**（pytesseractは本体のラッパー）
   - https://github.com/UB-Mannheim/tesseract/wiki からインストーラーを入手
   - インストール時に **日本語データ(Japanese)** も選択
   - PATHに入らない場合は環境変数で実行パスを指定:
     ```cmd
     set TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
     ```

2. **Pythonパッケージをインストール**
   ```cmd
   python -m pip install -r requirements.txt
   ```
   ※ easyocr は初回実行時に学習モデルを自動ダウンロードします（数百MB）

3. **登録プレイヤーを設定**
   ```cmd
   copy config.example.json config.json
   ```
   `config.json` を開き、`teamA` / `teamB` の `members` に
   各プレイヤーのRiotIDゲーム名（#タグより前）を記入する。

### 実行

```cmd
python worker.py
```

3秒ごとに以下のような行が表示される:

```
[2026-06-24T14:00:00] サイクル 2.85秒  | Team A: HP390 生存4/4  | Team B: HP375 生存4/4
```

- **「サイクル ○秒」が3秒未満** → そのまま実用OK
- **3秒を超過** → 顔照合などの高速化を検討（次のステップ）

結果は `output.json` にも書き出される（次ステップでOverwolfが読む）。

---

## 今後のステップ（予定）

- **ステップ3**: output.json を localhost配信 → Overwolfポーリング表示
- **ステップ4**: C#プラグインでオーバーレイ起動時に自動起動
