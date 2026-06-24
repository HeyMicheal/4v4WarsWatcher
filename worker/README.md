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

## 今後のステップ（予定）

- **ステップ2**: 固定座標でプレイヤーリストを切り出し、OCRパイプライン接続
- **ステップ3**: localhost配信 → Overwolfポーリング表示
- **ステップ4**: C#プラグインでオーバーレイ起動時に自動起動
