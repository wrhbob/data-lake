"""utils.py — 共享工具函数（不依赖 MinerU schema）"""
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_config(name):
    """读 6_配置/<name>.json（2026-08-04 改：覆盖 → 合并）

    加载顺序：
      1. 根 6_配置/{name}.json（跨城通用综合关键词，必加载）
      2. per-city 6_配置/{name}.json（武汉独有，追加到根的）

    合并逻辑（针对 {key: {kw: [list]}} 结构）：
      - per-city 的 key 不在根 → 新增
      - per-city 的 key 在根 → kw 列表追加（不去重，让人工 review）
      - per-city 的非 kw 字段 → 覆盖根的

    用途：
      - section_titles.json / skip_keywords.json / caption_keywords.json 都走这
      - 武汉 6_配置 加自己独有词，根的通用词也保留
      - 不互相覆盖，避免成都/北京跑不动

    示例:
      根 = {"MARKET_MATERIAL": {"kw": ["市场信息价格", ...]}}
      武汉 = {"MARKET_MATERIAL": {"kw": ["海绵城市"]}, "MARKET_PC": {"kw": ["装配式建筑"]}}
      合并 = {"MARKET_MATERIAL": {"kw": ["市场信息价格", ..., "海绵城市"]}, "MARKET_PC": {"kw": ["装配式建筑"]}}
    """
    project_root = ROOT.parent
    root_path = project_root / "6_配置" / name
    per_city_path = ROOT / "6_配置" / name

    # 加载根（必加载）
    if root_path.exists():
        root_data = json.loads(root_path.read_text(encoding="utf-8"))
    else:
        root_data = {}

    # 加载 per-city（可选）
    if per_city_path.exists() and per_city_path != root_path:
        per_city_data = json.loads(per_city_path.read_text(encoding="utf-8"))
        # 合并: per-city 的 key 追加到根的，不替换
        for key, val in per_city_data.items():
            if key.startswith("_"):  # 注释字段
                root_data[key] = val
                continue
            if key not in root_data:
                root_data[key] = val  # 新增 key
            elif isinstance(val, dict) and isinstance(root_data[key], dict):
                # 都是 dict, 合并 kw 列表
                if "kw" in val and "kw" in root_data[key]:
                    # kw 列表追加（不去重，人工 review）
                    root_data[key]["kw"] = list(root_data[key]["kw"]) + list(val["kw"])
                # 其他字段覆盖
                for k, v in val.items():
                    if k != "kw":
                        root_data[key][k] = v
            else:
                # 非 dict, 直接覆盖（保守起见）
                root_data[key] = val
    return root_data


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


# ============================================================
# 城市 yaml schema 校验（2026-07-31 增：避免运行时才发现字段错）
# ============================================================

def validate_city_yaml(yaml_data, city_name=None):
    """校验城市 yaml 必需字段，避免运行时才报 NotImplementedError

    必需字段：
      - city: str
      - districts: list[str]
      - page_offset: int (可省略，自动算)
      - nm_end: dict[str, int]  (格式 {period: page_idx}，可省略自动算)

    不通过 → raise ValueError，列出缺什么字段
    """
    issues = []
    if "city" not in yaml_data:
        issues.append("缺字段 'city'")
    if "districts" not in yaml_data or not isinstance(yaml_data.get("districts"), list):
        issues.append("缺字段 'districts' (list[str])")
    if "page_offset" in yaml_data and not isinstance(yaml_data["page_offset"], int):
        issues.append(f"page_offset 应为 int，当前: {type(yaml_data['page_offset']).__name__}")
    if "nm_end" in yaml_data and not isinstance(yaml_data["nm_end"], dict):
        issues.append(f"nm_end 应为 dict[str, int]，当前: {type(yaml_data['nm_end']).__name__}")
    if "nm_end" in yaml_data and isinstance(yaml_data["nm_end"], dict):
        for period, end in yaml_data["nm_end"].items():
            if not isinstance(end, int):
                issues.append(f"nm_end.{period} 应为 int，当前: {end} ({type(end).__name__})")
    if issues:
        prefix = f"城市 yaml 校验失败 ({city_name or '?'})：\n  "
        raise ValueError(prefix + "\n  ".join(issues))
    return True
