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
HP_TH = 150

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


def read_name(img, cy):
    """指定行の名前をEasyOCRで読み取る（生の文字列）。"""
    crop = img.crop((NAME_X[0], cy - 15, NAME_X[1], cy + 16))
    crop = crop.resize((crop.width * 4, crop.height * 4), Image.LANCZOS)
    res = get_reader().readtext(np.array(crop), detail=0)
    return "".join(res).replace(" ", "")


# Windowsでサブプロセスのコンソール窓を出さないフラグ
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

_tess_err_logged = False


def _tesseract_digits(binimg):
    """
    二値化画像をtesseractに渡し、認識した数字文字列を返す。

    pytesseractを使わず直接呼び出す。一時ファイルはシステムtemp（ASCIIパス）に
    作り、作業ディレクトリもそこにすることで、日本語パス(D:\\仕事\\..)が
    tesseractのメッセージに混入してcp932デコードで壊れる問題を回避する。
    stdoutが空のときはstderrを安全にデコードして最初の1回だけ表示する。
    """
    global _tess_err_logged
    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, "hp.png")
        binimg.save(in_path)
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
        out = proc.stdout.decode("utf-8", errors="ignore").strip()
        if not out and proc.stderr and not _tess_err_logged:
            _tess_err_logged = True
            err = proc.stderr.decode("utf-8", errors="replace")
            print(f"\n[tesseract警告/エラー] return={proc.returncode}\n{err}")
        return out


def read_hp(img, cy):
    """指定行のHPをtesseractで読み取る（int または None）。"""
    crop = img.crop((HP_X[0], cy - 13, HP_X[1], cy + 14))
    binimg = _binarize(crop, HP_TH, scale=8)
    raw = _tesseract_digits(binimg)
    try:
        return int(raw)
    except ValueError:
        return None


_logged = set()  # 同じ種類のエラーは1回だけ詳細表示する


def _log_once(kind, exc):
    """エラーの詳細トレースを種類ごとに最初の1回だけ表示する。"""
    if kind in _logged:
        return
    _logged.add(kind)
    import traceback
    print(f"\n[OCRエラー: {kind}] {type(exc).__name__}: {exc}")
    print(traceback.format_exc())


def read_rows(img):
    """
    8行すべてを読み取り、[(name_raw, hp), ...] を返す。
    img は 1920x1080 のPIL.Image（RGB）。
    名前・HPは個別に例外処理し、片方が失敗してももう片方は読む。
    """
    rows = []
    for cy in ROW_CENTERS:
        try:
            name = read_name(img, cy)
        except Exception as e:
            name = ""
            _log_once("name(EasyOCR)", e)
        try:
            hp = read_hp(img, cy)
        except Exception as e:
            hp = None
            _log_once("hp(tesseract)", e)
        rows.append((name, hp))
    return rows


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
