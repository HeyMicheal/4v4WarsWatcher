"""
OCRワーカー本体

常駐してTFTの起動を待ち受け、TFTウィンドウをWGCでキャプチャしながら：
  1. 各行(名前画像)を匿名クラスタとしてNCCで追跡（並び替えに追従・確実）
  2. 名前未確定のクラスタをEasyOCRで読んで登録名へ照合し下書き（命名）
  3. 各プレイヤーのHPをtesseractで読み取り（一括読みで高速）
  4. 「誰が・画面のどのY位置で・何HPか」を http://127.0.0.1:<port>/stats で配信
名前リストはホーム画面から POST /config で受け取る。OCRが読めない時は
ホーム画面が GET /rows で行画像を取得し、POST /assign で手動対応できる。
チーム分け・色・合計・生存判定はすべてOverwolf側が担う。

実行:
    python worker.py     （Ctrl+Cで終了。自動起動はREADME参照）
"""

import json
import os
import sys
import time
from datetime import datetime

from PIL import Image
from windows_capture import WindowsCapture, Frame, InternalCaptureControl

import ocr_engine
from stats_server import start_stats_server

# list_windows.py で確認した正確なタイトルに合わせる
WINDOW_NAME = "League of Legends (TM) Client"

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
OUTPUT_PATH = os.path.join(HERE, "output.json")
LOG_PATH = os.path.join(HERE, "worker.log")

EXPECTED_SIZE = (1920, 1080)

# set DEBUG=1 で、フレーム保存と生OCR結果の表示を有効化
DEBUG = os.environ.get("DEBUG") == "1"


class _Tee:
    """print出力をコンソールとログファイルの両方へ流す。"""

    def __init__(self, stream, logfile):
        self.stream = stream
        self.logfile = logfile

    def write(self, data):
        if self.stream:  # pythonw起動時は stdout が None のことがある
            self.stream.write(data)
        self.logfile.write(data)
        self.logfile.flush()

    def flush(self):
        if self.stream:
            self.stream.flush()
        self.logfile.flush()


def setup_logging():
    """標準出力・標準エラーをworker.logにも記録する（毎回上書き）。"""
    logfile = open(LOG_PATH, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, logfile)
    sys.stderr = _Tee(sys.stderr, logfile)
    print(f"ログ出力先: {LOG_PATH}")


DEFAULT_CONFIG = {
    # 照合対象の全プレイヤー名（チーム区別なし）。チーム分け・色はOverwolf側が持つ。
    "names": [],
    "interval_seconds": 3,
    "fast_interval_seconds": 0.5,
    "http_port": 17653,
}


def extract_names(payload):
    """設定ペイロードから名前リストを取り出す。
    新形式の {"names": [...]} を優先し、旧形式の teamA/teamB.members にも対応する。"""
    if isinstance(payload.get("names"), list):
        return [str(n) for n in payload["names"] if n]
    names = []
    for key in ("teamA", "teamB"):
        team = payload.get(key)
        if isinstance(team, dict):
            for m in team.get("members", []):
                # members は文字列 or {"name": ...} のどちらもあり得る
                name = m.get("name") if isinstance(m, dict) else m
                if name:
                    names.append(str(name))
    return names


def load_config():
    """config.json を読み込む。なければ既定値（名前空）で起動し、ホーム画面からの設定を待つ。"""
    if not os.path.exists(CONFIG_PATH):
        print("config.json がありません。ホーム画面からの設定を待ちます。")
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    config = {**DEFAULT_CONFIG, **raw}
    # 旧形式（teamA/teamB）のconfig.jsonからも名前を拾えるようにする
    if not config.get("names"):
        config["names"] = extract_names(raw)
    return config


def save_config(config):
    """config を config.json に書き出す。"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# 行(名前画像)をクラスタに割り当てる最低NCC類似度（これ未満は別人扱い）
MIN_MATCH_NCC = 0.30
# 位置・HP・画像・OCRを更新してよい「確信できる一致」のNCC閾値。
# 弱い/誤った一致で正解クラスタを別の行の値で汚染しないためのガード。
CONFIDENT_NCC = 0.50
# OCR下書きを採用する最低スコア（誤命名防止のため高め）
INIT_MATCH_SCORE = 0.55
# 名前未確定のクラスタを各々EasyOCRする最大回数（これを使い切ったら手動待ち）
MAX_OCR_TRIES = 5
# 表示用の名前画像クロップ範囲（名前＋HPが入る帯。手動対応で人が読む用）
DISP_X = (1655, 1858)
# テンプレ照合のNCC合成の重み（名前マスク : タクティシャン肖像）。
# 名前を高めにして、同じ肖像の別人を名前で区別できるようにする。
NAME_W, TACT_W = 0.6, 0.4


def template_features(img, cy):
    """テンプレ照合用の特徴を作る: (名前白文字マスク, タクティシャン肖像)。"""
    return (ocr_engine.name_mask(img, cy), ocr_engine.tactician_feature(img, cy))


def template_ncc(feat, template):
    """特徴(名前,肖像)とテンプレートの合成NCC（名前0.6＋肖像0.4）。"""
    return (NAME_W * ocr_engine.ncc(feat[0], template[0])
            + TACT_W * ocr_engine.ncc(feat[1], template[1]))


def _png_b64(pil_img):
    """PIL画像をPNGのdata URL(base64)にする。ホーム画面の<img>でそのまま使える。"""
    import io
    import base64
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


class Cluster:
    """画面上の1人ぶんの名前画像（並び替えにNCCで追従する追跡単位）。"""

    def __init__(self, cid, template, crop):
        self.id = cid
        self.template = template   # 照合用テンプレ特徴 (名前マスク, 肖像)
        self.crop = crop           # 表示用RGB画像(PIL)
        self.ocr_name = None       # OCR下書きで付いた名前
        self.manual_name = None    # ホーム画面で手動指定された名前（最優先）
        self.guess_cand = None     # OCRの最良候補（重複解消前。下書きの素材）
        self.guess_score = 0.0
        self.ocr_tries = 0         # この行をOCRした回数
        self.last_y = None
        self.last_hp = None

    @property
    def name(self):
        # 手動指定があれば最優先、なければOCR下書き
        return self.manual_name or self.ocr_name


class PlayerTracker:
    """
    各行(名前画像)を匿名クラスタとしてNCCで追跡し、あとから名前を付ける。

    - 追跡: 全非空行をクラスタ化し、並び替えにNCCで追従（OCR不要・確実）。
      これで8人全員を最初から追跡でき、HPも位置で読める。
    - 命名(下書き): 名前未確定のクラスタをEasyOCRで読み、登録名へ照合して
      下書きする。複数こぼれても追跡は崩れない。
    - 命名(手動): ホーム画面で「行→プレイヤー名」を指定すると最優先で確定。
      OCRが読めなかった時のリカバリ手段。

    出力(/stats)は「名前の付いたクラスタの 名前・Y位置・HP」だけ。チーム分け・
    色・合計・生存判定はOverwolf側が担う。
    """

    def __init__(self, config):
        self.init_score = config.get("init_match_score", INIT_MATCH_SCORE)
        # 名前リストはスナップショットを持つ（外部のconfig辞書が後で書き換わっても影響を受けない）
        self.names = list(config.get("names", []))
        self.clusters = []   # Cluster のリスト
        self.next_id = 0

    def inherit(self, prev):
        """前のトラッカーからクラスタ（テンプレ・画像・命名・HP）を引き継ぐ。
        設定更新（メンバー追加など）でも全員分のEasyOCRをやり直さないため。"""
        if not prev:
            return
        for c in prev.clusters:
            nc = Cluster(c.id, c.template, c.crop)
            nc.ocr_tries = c.ocr_tries
            nc.last_y, nc.last_hp = c.last_y, c.last_hp
            # 名前は新しいリストに存在する物だけ引き継ぐ
            if c.manual_name in self.names:
                nc.manual_name = c.manual_name
            if c.ocr_name in self.names:
                nc.ocr_name = c.ocr_name
            if c.guess_cand in self.names:
                nc.guess_cand, nc.guess_score = c.guess_cand, c.guess_score
            self.clusters.append(nc)
            self.next_id = max(self.next_id, c.id + 1)

    def set_manual(self, cid, name):
        """ホーム画面からの手動指定を反映する（name=空/Noneで解除）。"""
        for c in self.clusters:
            if c.id == cid:
                c.manual_name = name if name else None
                return

    def reset_ocr(self, ids=None):
        """OCR状態を初期化してEasyOCRをやり直させる（スクショのタイミング不良の復旧用）。
        - ids=None（一括）: まだ名前が付いていない行だけ。正解は壊さない。
        - ids指定（行ごと）: その行だけ。手動指定も解除してOCRに任せる。"""
        n = 0
        for c in self.clusters:
            if ids is None:
                if c.name:        # 一括時は未命名のみ（正解を保護）
                    continue
            else:
                if c.id not in ids:
                    continue
                c.manual_name = None  # 指定再OCRはOCR結果に委ねる
            c.ocr_tries = 0
            c.guess_cand = None
            c.guess_score = 0.0
            c.ocr_name = None
            n += 1
        return n

    def register_slot(self, feat, crop, y, name):
        """指定位置のキャプチャ特徴を、その名前のテンプレートとして登録/変更する（手動）。
        - 同名の既存クラスタがあればテンプレを差し替え（変更）
        - 無ければ画像が近い既存を再利用、それも無ければ新規追加
        OCRに頼らず、人が見た画像をそのままテンプレートにできる。"""
        target = next((c for c in self.clusters
                       if name in (c.manual_name, c.ocr_name)), None)
        if target is None:
            best, best_sim = None, 0.0
            for c in self.clusters:
                s = template_ncc(feat, c.template)
                if s > best_sim:
                    best, best_sim = c, s
            if best is not None and best_sim >= CONFIDENT_NCC:
                target = best
            else:
                target = Cluster(self.next_id, feat, crop)
                self.next_id += 1
                self.clusters.append(target)
        # 名前の重複を防ぐ（他クラスタから同名を外す）
        for c in self.clusters:
            if c is not target:
                if c.manual_name == name:
                    c.manual_name = None
                if c.ocr_name == name:
                    c.ocr_name = None
        target.template = feat
        target.crop = crop
        target.last_y = y
        target.manual_name = name
        target.ocr_name = None
        target.ocr_tries = MAX_OCR_TRIES   # 以後OCR不要
        target.guess_cand = None
        target.guess_score = 0.0
        return target

    def slots_payload(self, img):
        """画面の各行（上から）の現在の名前画像を返す（位置からの手動登録用）。"""
        out = []
        for i, cy in enumerate(ocr_engine.ROW_CENTERS):
            m = ocr_engine.name_mask(img, cy)
            if ocr_engine.mask_is_empty(m):
                continue
            out.append({
                "slot": i,             # ROW_CENTERS のインデックス
                "top": len(out) + 1,   # 上から数えた順位（空行を除く）
                "image": _png_b64(self._crop_disp(img, i)),
            })
        return out

    def needs_slow(self):
        """OCR下書きを試している間は遅いモード（EasyOCRが重いため）。"""
        if not self.clusters:
            return True
        return any(c.name is None and c.ocr_tries < MAX_OCR_TRIES for c in self.clusters)

    def update(self, img):
        """1フレームを処理し、プレイヤーごとの位置・HPを返す。"""
        # テンプレ特徴(名前マスク, タクティシャン肖像)を各行で作る
        feats = [template_features(img, cy) for cy in ocr_engine.ROW_CENTERS]
        masks = [f[0] for f in feats]   # 名前マスク（空行判定にも使う）
        non_empty = [i for i, m in enumerate(masks) if not ocr_engine.mask_is_empty(m)]

        # 行→クラスタを合成NCCで1対1に割り当て（並び替えに追従）。{slot:(cluster,sim)}
        matches = self._match_slots(feats, non_empty)
        slot_cluster = {slot: c for slot, (c, _sim) in matches.items()}

        # 割当されなかった行は新規クラスタにする（人数上限まで）。新規は信頼扱い
        cap = max(len(self.names), 8)
        trusted = {slot for slot, (c, sim) in matches.items() if sim >= CONFIDENT_NCC}
        for slot in non_empty:
            if slot not in slot_cluster and len(self.clusters) < cap:
                c = Cluster(self.next_id, feats[slot], self._crop_disp(img, slot))
                c.last_y = ocr_engine.ROW_CENTERS[slot]
                self.next_id += 1
                self.clusters.append(c)
                slot_cluster[slot] = c
                trusted.add(slot)

        # 確信できる一致の行だけ位置を更新（弱い/誤った一致での汚染を防ぐ）
        for slot in trusted:
            slot_cluster[slot].last_y = ocr_engine.ROW_CENTERS[slot]

        # 確信できる行のHPを一括で読んで更新
        hps = ocr_engine.read_hps(img, list(trusted))
        for slot in trusted:
            if hps.get(slot) is not None:
                slot_cluster[slot].last_hp = hps[slot]

        # OCR下書き（確信できる行のうち未確定のものだけ。画像もこの時に取り直す）
        self._ocr_draft(img, {slot: slot_cluster[slot] for slot in trusted})
        # OCR下書き同士の名前重複を解消して確定
        self._resolve_names()

        named = sum(1 for c in self.clusters if c.name)
        return slot_cluster, {
            "updated": datetime.now().isoformat(timespec="seconds"),
            "named": named,
            "total": len(self.clusters),
            "players": self._players(),
        }

    def _match_slots(self, feats, non_empty):
        """非空行を既存クラスタに合成NCCで1対1割り当て。{slot: (Cluster, sim)} を返す。"""
        pairs = []
        for slot in non_empty:
            for c in self.clusters:
                pairs.append((template_ncc(feats[slot], c.template), slot, c))
        pairs.sort(key=lambda p: p[0], reverse=True)

        used_slots, used_ids, res = set(), set(), {}
        for sim, slot, c in pairs:
            if sim < MIN_MATCH_NCC:
                break
            if slot in used_slots or c.id in used_ids:
                continue
            res[slot] = (c, sim)
            used_slots.add(slot)
            used_ids.add(c.id)
        return res

    def _crop_disp(self, img, slot):
        """表示用の名前画像（名前＋HPの帯）を2倍に拡大して返す。"""
        cy = ocr_engine.ROW_CENTERS[slot]
        crop = img.crop((DISP_X[0], cy - 17, DISP_X[1], cy + 17))
        return crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)

    def _ocr_draft(self, img, slot_cluster):
        """名前未確定のクラスタをEasyOCRで読み、最良候補を下書きとして蓄える。
        命名済みクラスタの画像は凍結し、読み直す行だけ画像を更新する
        （正解プレイヤーの画像が別フレームで汚染されるのを防ぐ）。"""
        for slot, c in slot_cluster.items():
            if c.name is not None or c.ocr_tries >= MAX_OCR_TRIES:
                continue
            c.crop = self._crop_disp(img, slot)  # 読む瞬間の画像を表示用に保存
            c.ocr_tries += 1
            raw = ocr_engine.read_name(img, ocr_engine.ROW_CENTERS[slot])
            cand, score = ocr_engine.fuzzy_match(raw, self.names)
            if cand and score > c.guess_score:
                c.guess_cand, c.guess_score = cand, score
            if not (cand and score >= self.init_score):
                print(f"  未照合 行{c.id}: OCR=[{raw}] 最良=[{cand}] 類似{score:.2f}")

    def _resolve_names(self):
        """手動指定を最優先に、OCR下書きを名前が重複しないよう一意に割り当てる。"""
        taken = set(c.manual_name for c in self.clusters if c.manual_name)

        # 下書き候補をスコア降順に、未使用の名前へ割り当てる
        cands = sorted(
            ((c.guess_score, c) for c in self.clusters
             if not c.manual_name and c.guess_cand and c.guess_score >= self.init_score),
            key=lambda x: x[0], reverse=True,
        )
        assigned_ids = set()
        for _, c in cands:
            if c.guess_cand in taken:
                c.ocr_name = None
                continue
            c.ocr_name = c.guess_cand
            taken.add(c.guess_cand)
            assigned_ids.add(c.id)
        # 下書きに採用されなかったクラスタのOCR名はクリア（手動は触らない）
        for c in self.clusters:
            if not c.manual_name and c.id not in assigned_ids:
                c.ocr_name = None

        # 消去法: 未使用の名前と未命名のクラスタがそれぞれ1つなら確定
        unnamed = [c for c in self.clusters if not c.name]
        free_names = [n for n in self.names if n not in taken]
        if len(free_names) == 1 and len(unnamed) == 1:
            unnamed[0].ocr_name = free_names[0]

    def _players(self):
        """名前の付いたクラスタの 名前・Y位置・HP を返す。"""
        return [
            {"name": c.name, "y": c.last_y, "hp": c.last_hp}
            for c in self.clusters if c.name
        ]

    def rows_payload(self):
        """ホーム画面の手動対応用に、各クラスタの画像とOCR下書きを返す。"""
        ordered = sorted(
            self.clusters,
            key=lambda c: c.last_y if c.last_y is not None else 9999,
        )
        return [
            {
                "id": c.id,
                "image": _png_b64(c.crop) if c.crop else None,
                "guess": c.ocr_name,       # OCR下書き（採用済み）
                "manual": c.manual_name,   # 手動指定
                "name": c.name,            # 実効名
                "hp": c.last_hp,
            }
            for c in ordered
        ]


def frame_to_image(frame: Frame) -> Image.Image:
    """WGCフレーム(BGRA numpy)をPIL RGB画像に変換。"""
    buf = frame.frame_buffer  # (H, W, 4) BGRA
    rgb = buf[:, :, [2, 1, 0]]  # BGR→RGB（アルファ除外）
    return Image.fromarray(rgb)


def window_exists(name):
    """指定タイトルを含むウィンドウが存在するか。"""
    try:
        import pygetwindow as gw
        return any(name in t for t in gw.getAllTitles() if t)
    except Exception:
        return False


def wait_for_window(name, poll=2):
    """指定ウィンドウが現れるまで待つ（Ctrl+Cで中断可能）。"""
    while not window_exists(name):
        time.sleep(poll)


def main():
    setup_logging()
    ocr_engine.setup_tessdata()  # tesseractとtessdataを解決（ログに記録される）
    config = load_config()
    # 初期化中（EasyOCRで重い）と高速モードで間隔を分ける
    init_interval = config.get("interval_seconds", 3)
    fast_interval = config.get("fast_interval_seconds", 0.5)
    http_port = config.get("http_port", 17653)
    print(f"設定読み込み完了。初期化中{init_interval}秒 / 高速モード{fast_interval}秒間隔。")
    print(f"ウィンドウ: '{WINDOW_NAME}'")

    latest = {"stats": {"players": []}, "rows": [], "slots": []}
    state = {"last": 0.0, "warned_size": False, "tracker": PlayerTracker(config),
             "pending_assign": {}, "pending_reocr": False, "pending_reocr_ids": None,
             "pending_register": [], "slots_t": 0.0}

    def on_register(payload):
        """ホーム画面の「位置から登録」。{slot, name} を次の更新で反映する。"""
        try:
            slot = int(payload["slot"])
        except (KeyError, ValueError, TypeError):
            return
        name = payload.get("name") or None
        if name:
            state["pending_register"].append((slot, name))
            print(f"位置からの登録を受信: 行{slot} → {name}")

    def on_reocr(payload=None):
        """ホーム画面の「OCR再実行」。次の更新でOCR状態を初期化する。
        payload に ids があれば、その行だけ読み直す（なければ未命名を一括）。"""
        ids = (payload or {}).get("ids")
        state["pending_reocr"] = True
        state["pending_reocr_ids"] = set(int(i) for i in ids) if ids else None
        print(f"OCR再実行の要求を受信（対象: {'指定行' if ids else '未命名一括'}）")

    def on_assign(payload):
        """ホーム画面で手動指定された {行ID: 名前} を、次の更新で反映するため貯める。
        （別スレッドからトラッカーを直接いじらず、キャプチャ側で適用する）"""
        mapping = payload.get("mappings", payload) or {}
        for cid, name in mapping.items():
            try:
                state["pending_assign"][int(cid)] = name or None
            except (ValueError, TypeError):
                pass
        print(f"手動対応を受信: {len(mapping)}件")

    def on_config(payload):
        """ホーム画面から送られた名前リストを反映し、config.jsonを更新する。"""
        new_names = extract_names(payload)
        if new_names == config.get("names"):
            return config  # 同じ内容の再送はトラッカーを作り直さない（再OCR回避）
        config["names"] = new_names
        # チーム分け・色はOverwolf側が持つので、ここには残さない
        config.pop("teamA", None)
        config.pop("teamB", None)
        save_config(config)
        # 新しい名前リストでトラッカーを作り直すが、続投する名前のテンプレ等は引き継ぐ
        tracker = PlayerTracker(config)
        tracker.inherit(state["tracker"])
        state["tracker"] = tracker
        print(f"設定を更新しました（{len(new_names)}人）。"
              f"クラスタ保持: {len(tracker.clusters)}個")
        return config

    # 最新の集計をHTTPで配信し、設定/手動対応のPOSTも受け付ける（Overwolfと連携）。
    # ホーム画面からの /config を取りこぼさないよう、重いEasyOCRより先に起動する。
    start_stats_server(
        http_port, lambda: latest["stats"], on_config,
        get_rows=lambda: latest["rows"], on_assign=on_assign, on_reocr=on_reocr,
        get_slots=lambda: latest["slots"], on_register=on_register,
    )
    print(f"HTTP配信: http://127.0.0.1:{http_port}/stats "
          f"（/config 設定, /rows 行画像, /assign 手動対応）")

    # EasyOCRのモデル読み込みは重い。初回フレームの待ち時間にならないよう、
    # TFT待ちの間に先にロードしておく（この間も /config は受け付けられる）。
    print("EasyOCRを準備中…（初回のみ時間がかかります）")
    t0 = time.time()
    ocr_engine.get_reader()
    print(f"EasyOCR準備完了（{time.time() - t0:.1f}秒）")

    def process_frame(frame):
        """1フレームを処理して集計・配信する。"""
        tracker = state["tracker"]
        # フレームは高頻度で届くので、一定間隔に1回だけ処理する
        # OCR下書き中（重い）は長め、命名が落ち着いたら高速に回す
        interval = init_interval if tracker.needs_slow() else fast_interval
        now = time.time()
        if now - state["last"] < interval:
            return
        state["last"] = now

        # ホーム画面からの手動対応をこのスレッドで反映する
        if state["pending_assign"]:
            for cid, name in state["pending_assign"].items():
                tracker.set_manual(cid, name)
            state["pending_assign"] = {}

        # ホーム画面からの「OCR再実行」を反映する
        if state["pending_reocr"]:
            n = tracker.reset_ocr(state.get("pending_reocr_ids"))
            state["pending_reocr"] = False
            state["pending_reocr_ids"] = None
            print(f"OCRを再実行します（対象 {n}クラスタ）")

        img = frame_to_image(frame)
        if img.size != EXPECTED_SIZE and not state["warned_size"]:
            print(f"警告: 解像度が {img.size} です（{EXPECTED_SIZE}前提）。座標がずれる可能性。")
            state["warned_size"] = True

        # ホーム画面からの「位置から登録」を、この瞬間のフレームで反映する
        if state["pending_register"]:
            for slot, name in state["pending_register"]:
                cy = ocr_engine.ROW_CENTERS[slot]
                feat = template_features(img, cy)
                crop = tracker._crop_disp(img, slot)
                tracker.register_slot(feat, crop, cy, name)
                print(f"位置から登録: 行{slot}(上から) → {name}")
            state["pending_register"] = []

        # デバッグモード（set DEBUG=1）: 最初のフレームを保存し、割り当てを表示
        if DEBUG and not state.get("saved_frame"):
            dbg_path = os.path.join(HERE, "debug_frame.png")
            img.save(dbg_path)
            print(f"デバッグ: フレームを保存しました → {dbg_path}（解像度 {img.size}）")
            state["saved_frame"] = True

        # OCR処理は例外で全体を落とさず、詳細をログに残して継続する
        try:
            t0 = time.time()
            slot_cluster, stats = tracker.update(img)
            if DEBUG:
                print("--- 行→クラスタ割り当て ---")
                for slot in range(len(ocr_engine.ROW_CENTERS)):
                    c = slot_cluster.get(slot)
                    if c:
                        print(f"  slot{slot}: 行{c.id} 名前=[{c.name}] HP=[{c.last_hp}]")
            dt = time.time() - t0
        except Exception:
            import traceback
            tb = traceback.format_exc()
            print("OCR処理でエラーが発生しました（詳細は worker_error.log）:")
            print(tb)
            with open(os.path.join(HERE, "worker_error.log"), "a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.now().isoformat()} ---\n{tb}\n")
            return

        # HTTP配信用に最新の集計と行画像を更新し、集計はファイルにも残す
        latest["stats"] = stats
        latest["rows"] = tracker.rows_payload()
        # 位置一覧（手動登録用）は重いので1秒に1回だけ更新する
        if now - state["slots_t"] >= 1.0:
            latest["slots"] = tracker.slots_payload(img)
            state["slots_t"] = now
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)

        print(
            f"[{stats['updated']}] サイクル {dt:.2f}秒  "
            f"| 命名 {stats['named']}/{stats['total']}人"
        )
        if dt > interval:
            print(f"  ※ サイクル({dt:.2f}秒)が間隔({interval}秒)を超過しています")

    def run_session():
        """TFTウィンドウをキャプチャする（TFTが閉じるまでブロックする）。"""
        capture = WindowsCapture(
            cursor_capture=None,
            draw_border=None,
            monitor_index=None,
            window_name=WINDOW_NAME,
        )

        @capture.event
        def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl):
            process_frame(frame)

        @capture.event
        def on_closed():
            print("キャプチャを終了しました")

        capture.start()

    # 常駐ループ: TFTを待ち受け、起動中はキャプチャ、終了したら待機に戻る
    print("TFTの起動を待っています…（Ctrl+Cで終了）")
    try:
        while True:
            wait_for_window(WINDOW_NAME)
            print("TFTを検出。キャプチャを開始します。")
            # 新しい試合用に状態をリセット（クラスタ・テンプレートを取り直す）
            state["last"] = 0.0
            state["warned_size"] = False
            state.pop("saved_frame", None)
            state["tracker"] = PlayerTracker(config)
            state["pending_assign"] = {}
            state["pending_reocr"] = False
            state["pending_reocr_ids"] = None
            state["pending_register"] = []
            try:
                run_session()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                # Ctrl+Cはライブラリ内で別例外に包まれることがあるので文字列で判定
                if "KeyboardInterrupt" in str(e):
                    raise KeyboardInterrupt
                print(f"キャプチャが停止しました: {e}")
            print("TFTの終了を待っています…")
            time.sleep(3)
    except KeyboardInterrupt:
        print("終了します。")


if __name__ == "__main__":
    main()
