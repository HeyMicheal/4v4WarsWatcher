# worker-launcher（Overwolfプラグイン）

オーバーレイ起動時にOCRワーカー（`worker/worker.py`）を起動し、終了時に停止するための
小さなC#プラグイン。OverwolfはJSから直接プロセスを起動できないため、この**DLL**を介す。

## 仕組み

```
TFT起動を検知（background.js）
   → getExtraObject("worker-launcher") でプラグイン取得
   → Launch(python, "worker.py", workerDir) でワーカー起動
TFT終了
   → Kill() でワーカー停止
```

ワーカーのフォルダ・Pythonコマンドは**ホーム画面の「ワーカー起動設定」**で指定する。

## ビルド手順（Windows）

DLLをビルドするには .NET SDK か Visual Studio が必要。

### 方法A: dotnet CLI（おすすめ）

1. [.NET SDK](https://dotnet.microsoft.com/download) をインストール
2. このフォルダで：
   ```cmd
   dotnet build WorkerLauncher.csproj -c Release
   ```
   ※ net48 をビルドするには「.NET Framework 4.8 Developer Pack」が必要な場合あり
   （[ダウンロード](https://dotnet.microsoft.com/download/dotnet-framework/net48)）
3. 生成物：`bin\Release\worker-launcher.dll`

### 方法B: Visual Studio

1. `WorkerLauncher.csproj` を開く
2. 構成を **Release / x64** にしてビルド

## 配置

1. ビルドした `worker-launcher.dll` を、Overwolfアプリの **`plugins/worker-launcher.dll`** に置く
   （`manifest.json` の `data.extra-objects` がこのパスを指している）
2. **重要**: DLLを右クリック→プロパティ→「**ブロックの解除**」にチェック
   （ダウンロード由来のDLしはWindowsがブロックし、読み込みに失敗するため）
3. Overwolfでアプリを再読み込み

## 使い方

1. ホーム画面の「ワーカーフォルダ」に `worker` フォルダの絶対パスを入力
   （例: `D:\projects\4v4WarsWatcher\worker`）
2. Pythonは通常 `pythonw`（PATHにある前提）。フルパス指定も可
3. TFTを起動すると、オーバーレイと一緒にワーカーが自動で立ち上がる

## トラブルシュート

- **プラグイン取得に失敗** → DLLのパス／ブロック解除／x64ビルドを確認
- **ワーカーが起動しない** → ホーム画面のワーカーフォルダ、Pythonの指定を確認
- ログは `worker/worker.log` に残る
