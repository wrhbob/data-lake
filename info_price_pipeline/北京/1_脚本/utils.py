"""utils.py — 共享工具函数（不依赖 MinerU schema）"""
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_config(name):
    """读 6_配置/<name>.json"""
    return json.loads((ROOT / "6_配置" / name).read_text(encoding="utf-8"))


def city_to_code(city_name):
    """成都 → cd, 重庆 → cq, 湖北 → hb"""
    codes = load_config("city_codes.json")
    if city_name in codes:
        return codes[city_name]
    raise ValueError(f"未知城市 '{city_name}'，加到 6_配置/city_codes.json")


def save_cache(city_code, period, page, data):
    """存到 3_中间产物/{code}_{period}_ocr/page_{NNN}.json"""
    p = ROOT / "3_中间产物" / f"{city_code}_{period}_ocr" / f"page_{page:03d}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cache(city_code, period, page):
    """读缓存，返 None 表示无"""
    p = ROOT / "3_中间产物" / f"{city_code}_{period}_ocr" / f"page_{page:03d}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def log_error(msg):
    """追写 5_日志/error.log"""
    p = ROOT / "5_日志" / "error.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
