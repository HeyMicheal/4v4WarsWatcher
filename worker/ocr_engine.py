"""
OCRエンジン（プレイヤーリスト読み取り）

TFT画面（1920x1080）の右側プレイヤーリストから、各行の名前とHPを読み取る。
- 名前: EasyOCR（日本語カタカナに強い）
- HP  : tesseract --psm 6（数字・マイナス・複数桁に強い）

座標は1920x1080固定。プレイヤーリストは右端に8行、72px間隔で並ぶ。
（kiai.png / capture.png で検証済みの座標）
"""

import difflib
import io
import os
import subprocess
from PIL import Image, ImageOps
import numpy as np

# WindowsでTesseractがPATHにない場合、環境変数 TESSERACT_CMD で実行パスを指定できる。
# 例: set TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
TESS_CMD = os.environ.get("TESSERACT_CMD", "tesseract")

# TESSDATA_PREFIX 未設定なら、tesseract.exe と同じフォルダの tessdata を自動指定。
# （これが無いと eng.traineddata が見つからずエラーになる）
if os.environ.get("TESSERACT_CMD") and not os.environ.get("TESSDATA_PREFIX"):
    _tessdata = os.path.join(os.path.dirname(TESS_CMD), "tessdata")
    if os.path.isdir(_tessdata):
        os.environ["TESSDATA_PREFIX"] = _tessdata

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


def _tesseract_digits(binimg):
    """
    二値化画像をtesseractに標準入出力経由で渡し、認識文字列を返す。

    pytesseractを使わず直接呼び出すことで:
    - 一時ファイルを作らない（日本語パス問題を回避）
    - 標準エラーのcp932デコードでクラッシュしない（errors='ignore'）
    """
    buf = io.BytesIO()
    binimg.save(buf, format="PNG")

    # tesseract <入力> <出力> ...  で "-" は標準入出力を意味する
    cmd = [
        TESS_CMD, "-", "-",
        "--psm", "6",
        "-c", "tessedit_char_whitelist=-0123456789",
    ]
    proc = subprocess.run(
        cmd, input=buf.getvalue(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=_NO_WINDOW,
    )
    # 出力はASCII数字のみ。念のため壊れたバイトは無視してデコード
    return proc.stdout.decode("utf-8", errors="ignore").strip()


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
