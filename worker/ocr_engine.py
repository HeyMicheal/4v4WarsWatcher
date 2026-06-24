"""
OCRエンジン（プレイヤーリスト読み取り）

TFT画面（1920x1080）の右側プレイヤーリストから、各行の名前とHPを読み取る。
- 名前: EasyOCR（日本語カタカナに強い）
- HP  : tesseract --psm 6（数字・マイナス・複数桁に強い）

座標は1920x1080固定。プレイヤーリストは右端に8行、72px間隔で並ぶ。
（kiai.png / capture.png で検証済みの座標）
"""

import difflib
import os
import shutil
import subprocess
import tempfile
from PIL import Image, ImageOps
import numpy as np

# tesseract.exe のパス。setup_tessdata() で解決する。
# 環境変数 TESSERACT_CMD > PATH上のtesseract > よくあるインストール先 の順で探す。
TESS_CMD = os.environ.get("TESSERACT_CMD") or "tesseract"

# Windowsのよくあるインストール先（TESSERACT_CMD未設定・PATHにも無い場合の保険）
_COMMON_TESS_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


def _is_ascii(s):
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _ensure_ascii_tessdata(tessdata):
    """
    tessdataパスに日本語等の非ASCII文字が含まれる場合、tesseract(mingw)が
    扱えないため、必要な言語データをASCIIパスの一時フォルダへコピーして
    そのフォルダパスを返す。ASCIIならそのまま返す。
    """
    if _is_ascii(tessdata):
        return tessdata

    ascii_dir = os.path.join(tempfile.gettempdir(), "4v4_tessdata")
    os.makedirs(ascii_dir, exist_ok=True)
    for lang in ("eng.traineddata", "jpn.traineddata"):
        src = os.path.join(tessdata, lang)
        dst = os.path.join(ascii_dir, lang)
        if os.path.isfile(src) and not os.path.isfile(dst):
            shutil.copy2(src, dst)
    print(f"非ASCIIパスのため言語データをASCIIへコピー: {ascii_dir}")
    return ascii_dir


def setup_tessdata():
    """
    tesseract.exe を探し、その隣の tessdata を TESSDATA_PREFIX に強制設定する。

    - PCに永続設定された誤った TESSDATA_PREFIX があれば上書きする
    - パスに日本語が含まれると tesseract が読めないため、ASCIIパスへ退避する
    worker側でログ設定後に呼ぶことで、結果が worker.log にも残る。
    """
    global TESS_CMD

    # 1) tesseract.exe を解決
    cmd = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if not cmd or not os.path.isfile(cmd):
        for p in _COMMON_TESS_PATHS:
            if os.path.isfile(p):
                cmd = p
                break
    if cmd and os.path.isfile(cmd):
        TESS_CMD = cmd
    print(f"tesseract: {TESS_CMD}")

    # 2) 隣の tessdata を見つける
    tessdata = os.path.join(os.path.dirname(TESS_CMD), "tessdata")
    if not os.path.isdir(tessdata):
        print(f"警告: tessdataフォルダが見つかりません → {tessdata}")
        print("  Tesseract本体が未インストール、または別の場所にあります。")
        print("  set TESSERACT_CMD=（tesseract.exeのフルパス）で指定してください。")
        return

    # 3) 日本語パス対策（ASCIIへ退避）してから TESSDATA_PREFIX に強制設定
    tessdata = _ensure_ascii_tessdata(tessdata)
    prev = os.environ.get("TESSDATA_PREFIX")
    os.environ["TESSDATA_PREFIX"] = tessdata
    eng = os.path.join(tessdata, "eng.traineddata")
    print(f"TESSDATA_PREFIX = {tessdata}（eng有無: {os.path.isfile(eng)}）")
    if prev and prev != tessdata:
        print(f"  ※ 既存の誤った値を上書きしました: {prev}")

# 8行の中心Y座標（1920x1080固定）
ROW_CENTERS = [216, 288, 360, 432, 504, 576, 648, 720]

# 各行内のクロップ範囲（X）
NAME_X = (1716, 1810)   # 名前（左ポートレートと右HPボックスを除外）
HP_X = (1812, 1856)     # HPボックス（枠線を避けつつマイナス・3桁を含む）

# 二値化のしきい値（明るい文字を黒に反転）
NAME_TH = 170
HP_WHITE_TH = 130   # HP数字: min(R,G,B)がこれ以上を白文字とみなす（金枠を除外）

# HPの妥当範囲。これを外れたOCR結果は誤読として無効化する
# （このモードではHPは150程度まで上がりうる）
HP_MIN, HP_MAX = 0, 150

_reader = None  # EasyOCRリーダー（初回ロードが重いので使い回す）


def get_reader():
    """EasyOCRリーダーを遅延初期化して返す。"""
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(["ja", "en"], gpu=False, verbose=False)
    return _reader


def _binarize(crop, th, scale):
    """グレースケール→拡大→二値化（白文字を黒に）。"""
    g = ImageOps.grayscale(crop).resize(
        (crop.width * scale, crop.height * scale), Image.LANCZOS
    )
    return g.point(lambda p: 0 if p > th else 255)


def _binarize_white(crop, th, scale):
    """
    白い文字だけを抽出して二値化する（HP数字用）。

    各画素の min(R,G,B) で判定する。白文字はR=G=Bが高いのでminも高いが、
    金色のハイライト枠は黄色で青成分が低いためminが低く、除外できる。
    文字を黒、背景を白にしてtesseractに渡す。
    """
    big = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    arr = np.array(big)
    mn = arr.min(axis=2)  # 各画素のRGB最小値
    mask = (mn > th).astype("uint8") * 255  # 白文字=255
    return Image.fromarray(255 - mask)       # 反転して文字=黒


# 名前テンプレート照合用の設定
NAME_MASK_SIZE = (160, 40)   # マスクを正規化する固定サイズ
NAME_WHITE_TH = 130          # 名前の白文字抽出しきい値
NAME_EMPTY_RATIO = 0.02      # 白画素率がこれ未満なら「名前なし（空行）」とみなす


def name_mask(img, cy):
    """
    指定行の名前領域を白文字マスク(0/1のfloat32配列)にして固定サイズに正規化する。
    並び順が変わっても同じプレイヤーなら同じ画像になるため、テンプレート照合に使う。
    """
    crop = img.crop((NAME_X[0], cy - 15, NAME_X[1], cy + 16))
    arr = np.array(crop.resize(NAME_MASK_SIZE, Image.LANCZOS))
    mn = arr.min(axis=2)  # 白文字はmin(R,G,B)が高い
    return (mn > NAME_WHITE_TH).astype(np.float32)


def mask_is_empty(mask):
    """マスクの白画素率が低い（名前が無い空行）かどうか。"""
    return float(mask.mean()) < NAME_EMPTY_RATIO


def ncc(a, b):
    """2つのマスクの正規化相互相関(-1〜1)。1に近いほど同一。"""
    a = a - a.mean()
    b = b - b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d > 0 else 0.0


def read_name(img, cy):
    """指定行の名前をEasyOCRで読み取る（生の文字列）。"""
    crop = img.crop((NAME_X[0], cy - 15, NAME_X[1], cy + 16))
    crop = crop.resize((crop.width * 4, crop.height * 4), Image.LANCZOS)
    res = get_reader().readtext(np.array(crop), detail=0)
    return "".join(res).replace(" ", "")


# Windowsでサブプロセスのコンソール窓を出さないフラグ
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

_tess_err_logged = False


def _run_tesseract(image):
    """
    数字用の設定でtesseractを実行し、stdout全文を返す。

    pytesseractを使わず直接呼び出す。一時ファイルはシステムtemp（ASCIIパス）に
    作り、作業ディレクトリもそこにすることで、日本語パス(D:\\仕事\\..)が
    tesseractのメッセージに混入してcp932デコードで壊れる問題を回避する。
    stdoutが空のときはstderrを安全にデコードして最初の1回だけ表示する。
    """
    global _tess_err_logged
    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, "hp.png")
        image.save(in_path)
        cmd = [
            TESS_CMD, in_path, "stdout",
            "--psm", "6",
            "-c", "tessedit_char_whitelist=-0123456789",
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW,
            cwd=td,  # 作業ディレクトリをASCIIパスにする
        )
        out = proc.stdout.decode("utf-8", errors="ignore")
        if not out.strip() and proc.stderr and not _tess_err_logged:
            _tess_err_logged = True
            err = proc.stderr.decode("utf-8", errors="replace")
            print(f"\n[tesseract警告/エラー] return={proc.returncode}\n{err}")
        return out


def _parse_hp(text):
    """文字列をHPとして解釈する。範囲外・解釈不能は None。"""
    try:
        hp = int(text)
    except (ValueError, TypeError):
        return None
    return hp if HP_MIN <= hp <= HP_MAX else None


def _hp_image(img, cy):
    """指定行のHPボックスを白さベースで二値化した画像を返す。"""
    crop = img.crop((HP_X[0], cy - 13, HP_X[1], cy + 14))
    return _binarize_white(crop, HP_WHITE_TH, scale=8)


def read_hp(img, cy):
    """指定行のHPを1つ読み取る（int または None）。"""
    return _parse_hp(_run_tesseract(_hp_image(img, cy)).strip())


# 一括読みで各HPボックスを縦に連結する際の隙間（行が混ざらないように）
_HP_STACK_GAP = 40


def read_hps(img, slots):
    """
    複数スロットのHPを1回のtesseract呼び出しでまとめて読む（高速）。

    各HPボックスを縦に隙間を空けて1枚に連結し、tesseractに複数行として
    読ませる。返り値は {slot: hp(or None)}。
    出力行数がスロット数と合わない場合は個別読みにフォールバックする。
    """
    if not slots:
        return {}

    images = [_hp_image(img, ROW_CENTERS[s]) for s in slots]
    width = max(im.width for im in images)
    height = sum(im.height for im in images) + _HP_STACK_GAP * (len(images) - 1)
    canvas = Image.new("L", (width, height), 255)  # 白背景
    y = 0
    for im in images:
        canvas.paste(im, (0, y))
        y += im.height + _HP_STACK_GAP

    lines = [ln for ln in _run_tesseract(canvas).splitlines() if ln.strip()]
    if len(lines) == len(slots):
        return {s: _parse_hp(lines[i]) for i, s in enumerate(slots)}

    # 行数がずれたら安全側で個別に読み直す
    return {s: read_hp(img, ROW_CENTERS[s]) for s in slots}


def fuzzy_match(ocr_name, candidates):
    """
    OCRした名前を候補リストと照合し、(最も近い候補, 類似度) を返す。
    candidates が空なら (None, 0.0)。
    """
    best, best_score = None, -1.0
    for c in candidates:
        score = difflib.SequenceMatcher(None, ocr_name, c).ratio()
        if score > best_score:
            best_score = score
            best = c
    return best, max(best_score, 0.0)
