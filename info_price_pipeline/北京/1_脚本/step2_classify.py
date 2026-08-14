"""step2_classify.py - 章节分类（按目录页解析章节-页码映射）

目的：
  result.json → classified.json（每 table 加 section 字段）

核心逻辑：
  1. 扫目录页（page 0-3）找章节标题 + 页码
     例: "市场信息价格 …… (50)" → ("市场信息价格", 50)
  2. 给章节名打 SKIP/MARKET/PC 标签
  3. 构造 page → section 范围映射
  4. 每个 table 按 page_idx 落章节
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ROOT, load_config


def load_keywords(city=None):
    """加载 SKIP/MARKET/PC/NEW_MATERIAL 关键词（2026-07-28 拆 NM 出独立 section）

    2026-07-30 P1-2 增：cities.{city}.additional_skip 平铺到 skip_kw
      - 背景：categories 是按"关键词类型"分组（法规/走势/费用）只覆盖已分类章节；
              但北京 06 toc page 46-48「人工工日市场价格信息」3 章无关键词类型归属，
              step2 走 page_to_section 时被外层 chapter 一（page 10-48）扩到 MARKET。
      - 修：从 skip_keywords.json["cities"][city] 读 additional_skip 列表平铺进 skip_kw
      - 未配 cities 字段的旧版 JSON 兼容（skip_kw 不变）
    """
    skip_full = load_config("skip_keywords.json")
    skip = skip_full.get("categories", {})
    sections = load_config("section_titles.json")

    skip_kw = set()
    for v in skip.values():
        skip_kw.update(v)

    # 2026-07-30 P1-2：读 cities.{city}.additional_skip 平铺
    if city:
        cities_cfg = skip_full.get("cities", {})
        city_cfg = cities_cfg.get(city, {})
        for kw in city_cfg.get("additional_skip", []):
            skip_kw.add(kw)

    market_kw = set()
    market_kw.update(sections["MARKET_MATERIAL"]["kw"])
    # MARKET_NEW_MATERIAL 已拆出独立章节（2026-07-28 改）

    pc_kw = set(sections["MARKET_PC"]["kw"])
    new_material_kw = set(sections["MARKET_NEW_MATERIAL"]["kw"])

    return skip_kw, market_kw, pc_kw, new_material_kw


def parse_toc(content_list, toc_pages=4, page_offset=0, toc_format=None):
    """扫目录页（page 0-3）找 (章节名, 起始页码) 列表

    正则:
      - trailing 格式（成都式）: "章节名 ..." + "(页码)" 或 "章节名 ... (页码)"
        例: "市场信息价格 …… (50)" → ("市场信息价格", 50)
      - leading 格式（重庆式）: "页码 章节名"
        例: "60 综合价格信息" → ("综合价格信息", 60)

    2026-07-28 改：末尾补 END_OF_TOC sentinel
      - 末章节没有 next_toc_start - 1 作 end，会延伸到所有后续页
      - 解决：拿 content_list 最大 page_idx - page_offset 作末章节 end（印刷页单位）
      - 9999 sentinel 不再用，由 toc 末尾 sentinel 替代
      - min(, 9998) 防 OCR 异常值

    2026-07-29 改：加 toc_format 参数
      - None (默认): 先试 trailing，0 命中再试 leading（向后兼容）
      - "leading": 先试 leading，0 命中再试 trailing（重庆 06 用）
        来源：yaml.toc_format 配置（重庆 = "leading"）
        优点：未来重庆 07/08 不改 yaml 自动适配（重庆一直是 leading）
    """
    toc = []
    # 2026-07-29 修（仅北京版）：
    #   1. 前括号 [\(（] 改成 [\(（]? 可选（北京 OCR 缺括号）
    #   2. ellipsis 段改成 (?:…\s*){1,} 允许 ellipsis 间有空格
    #      例："二、市场参考价… …49"（2 个 ellipsis 中间有空格）
    #      原正则 […\.]{2,} 要求连续 2+ ellipsis，匹不上
    # 不影响重庆版（重庆走 leading pattern）
    trailing_pattern = re.compile(r"^(.+?)(?:…\s*){1,}[\(（]?\s*(\d+)\s*[\)）]?")
    leading_pattern = re.compile(r"^(\d+)\s+(.+)")
    min_start_page = 10  # 1-9 是文章编号不是印刷页码

    def _try_trailing():
        out = []
        for item in content_list:
            if item.get("type") != "text":
                continue
            page = item.get("page_idx", 0)
            if page >= toc_pages:
                continue
            text = item.get("text", "").strip()
            m = trailing_pattern.match(text)
            if m:
                out.append((m.group(1).strip(), int(m.group(2))))
        return out

    def _try_leading():
        out = []
        for item in content_list:
            if item.get("type") != "text":
                continue
            page = item.get("page_idx", 0)
            if page >= toc_pages:
                continue
            text = item.get("text", "").strip()
            m = leading_pattern.match(text)
            if m:
                start_page = int(m.group(1))
                if start_page >= min_start_page:
                    out.append((m.group(2).strip(), start_page))
        return out

    if toc_format == "leading":
        toc = _try_leading()
        if not toc:
            toc = _try_trailing()
    else:
        toc = _try_trailing()
        if not toc:
            toc = _try_leading()
    # 末尾 sentinel：content_list 全量 item 的最大 page_idx（不限 type，包括 text/header/footer）
    max_page_idx = max(
        (item.get("page_idx", 0) for item in content_list),
        default=9999
    )
    max_page_idx = min(max_page_idx, 9998)  # 防 OCR 异常值（如 page_idx=99999）
    toc.append(("END_OF_TOC", max_page_idx - page_offset))
    return toc


def classify_section_name(name, skip_kw, market_kw, pc_kw, new_material_kw):
    """给章节名打 SKIP / NEW_MATERIAL / MARKET / PC 标签（2026-07-28 改 NM 独立）

    顺序：SKIP > NEW_MATERIAL > MARKET > PC > UNKNOWN
    """
    for kw in skip_kw:
        if kw in name:
            return "SKIP"
    for kw in new_material_kw:
        if kw in name:
            return "NEW_MATERIAL"
    for kw in market_kw:
        if kw in name:
            return "MARKET"
    for kw in pc_kw:
        if kw in name:
            return "PC"
    return "UNKNOWN"


def compute_page_offset(content_list, toc, city=None):
    """基于 PC/MARKET 章节首张表反推 page_offset（2026-07-28 增）

    2026-07-29 改：yaml 配 page_offset 优先级最高（重庆 06 实测 offset=2）
      - 自动算可能匹到章节小标题（"一、水泥及商品混凝土" 6 列 vs 真实数据表 6 列）
      - 自动算可能匹到 KPI 4 列表（min_cols=5 后）
      - 用户 yaml 配的是手测实际值，优先级最高
      - 仅当 yaml 未配 / 配 0 时才走自动算

    关键洞察（用户澄清 2026-07-28）：
      - TOC 页码 = PDF 内嵌页码（按图片里的页码）
      - content_list page_idx = MinerU 0-indexed
      - 偏移稳定（成都 06 = +3，重庆 06/湖北 06 等可能不同）

    锚点选择（不用"市场信息价"章节名，因为不同城市 toc 章节名不同）：
      1. 优先：toc 里第一个 PC 章节的首张数据表
      2. 备选：toc 里第一个 MARKET 章节的首张数据表
      3. fallback：3（成都 06 实测）

    成都 06 验证：
      - PC 章节 toc=169 → first PC data table page_idx=172 → offset=3 ✓
      - 注：用 caption 关键字判定数据表（不能用 section 字段，因为此函数在 section 分类前调用）
    """
    # 优先级 1：yaml 配 page_offset（用户手测实际值，最准）
    if city:
        try:
            yaml_data = load_city_yaml(city)
            yaml_offset = yaml_data.get("page_offset")
            if yaml_offset is not None and int(yaml_offset) != 0:
                print(f"[step2] yaml 配 page_offset = {yaml_offset}（优先级最高，跳过自动算）")
                return int(yaml_offset)
        except (FileNotFoundError, ValueError):
            pass
    # 用 caption 关键字判定数据表（造价指数/走势图/租赁价格等不是数据表）
    skip_caption_kw = [
        "价格指数", "造价指数", "材料价格指数",  # 造价指标章节
        "走势图", "趋势图", "折线图",  # 价格走势章节
        "机械设备", "周转材料", "市场租赁", "租赁价格",  # 租赁章节
        "厂商报价", "供应商报价", "企业报价",  # 厂商报价章节
    ]
    # 2026-07-29 增：跳过 toc 中的 meta 段（使用说明/索引/目录）— 这些段不是真 MARKET，
    # 它们在 toc 里出现很早 (toc_start<50) 含 "价格信息" 关键字会误匹
    skip_toc_name_kw = ["使用说明", "索引", "目录", "监测点名单", "排名不分先后"]

    # 优先级 1：PC 章节
    for name, toc_start in toc:
        if "PC" not in name and "构件" not in name:
            continue
        if any(s in name for s in skip_toc_name_kw):  # 2026-07-29 P10 修：跳 meta 段
            continue
        first_p = _find_first_table_in_toc_range(content_list, toc_start, page_window=10, skip_caption_kw=skip_caption_kw, min_cols=5)
        if first_p is not None:
            return first_p - toc_start

    # 优先级 2：MARKET 章节（关键词放宽："材料价格/市场信息价/价格信息/建筑市场/综合价格信息"等）
    # 2026-07-29 增："综合价格信息"（重庆 toc 实际章节名）
    # 2026-07-29 增：skip "价格信息使用说明" / "价格信息索引" / "监测点名单" 等 meta 段
    market_kws = ["市场信息价", "材料价格", "价格信息", "建筑市场", "建材价格", "综合价格信息"]
    for name, toc_start in toc:
        if not any(kw in name for kw in market_kws):
            continue
        if any(s in name for s in skip_toc_name_kw):  # 2026-07-29 P10 修：跳 meta 段
            continue
        first_p = _find_first_table_in_toc_range(content_list, toc_start, page_window=10, skip_caption_kw=skip_caption_kw, min_cols=5)
        if first_p is not None:
            return first_p - toc_start

    return 3  # fallback（成都 06 实测）


def _find_first_table_in_toc_range(content_list, toc_start, page_window=10, skip_caption_kw=None, min_cols=1):
    """找 toc_start ±page_window 范围内最早的"数据表" page_idx

    防误判（2026-07-28）：用 caption 关键字跳过非数据章节表
    原因：成都 06 toc_start=169±10 范围内含"造价指标"章节表（page=168-171），
          这些不是 PC 章节数据表，但 page_idx 更小会误判 first_p
    2026-07-29 增：min_cols 过滤（重庆 toc_start=60±10 范围内含"政府指标"表 4 列，
          不是市场价格数据表）
    """
    if skip_caption_kw is None:
        skip_caption_kw = []
    first_p = None
    for item in content_list:
        if item.get("type") != "table":
            continue
        # caption 关键字过滤
        cap = item.get("table_caption", [])
        cap_text = cap[0] if isinstance(cap, list) and cap else (cap if isinstance(cap, str) else "")
        if any(kw in cap_text for kw in skip_caption_kw):
            continue
        # 2026-07-29 增：min_cols 过滤（重庆政府指标表 4 列）
        if min_cols > 1:
            body = item.get("table_body", "") or ""
            # 粗略从 HTML 数第一行 td 数（>1 行有内容）
            import re
            first_tr_match = re.search(r"<tr>(.*?)</tr>", body, re.DOTALL)
            if first_tr_match:
                td_count = len(re.findall(r"<td", first_tr_match.group(1)))
                if td_count < min_cols:
                    continue
        p = item.get("page_idx", 0)
        if toc_start - 1 <= p <= toc_start + page_window:
            if first_p is None or p < first_p:
                first_p = p
    return first_p


def compute_nm_end(content_list, toc, page_offset, city=None):
    """NM 章节实际 end = yaml 配 nm_end + 经验 fallback（2026-07-28 增）

    关键洞察（用户澄清）：
      - 厂商报价 ≠ 新型材料（不同概念）
      - NM 章节范围严格 = TOC 标注的章节，end = 章节内最后一张表的 page_idx
      - 后续 page_idx+1 起是别的章节（如厂商报价），不属于 NM

    优先级：
      1. yaml `nm_end` 字段（最准，按城市配）
      2. 经验 fallback = toc_start + page_offset + 8（覆盖大部分 NM 章节 6-10 页）
      3. NM 章节内表最大 page_idx（仅当 NM 不是 toc 最后一项时准，toc 最后一项时=9999 错）

    为什么 fallback 是 +8：成都 06 NM 6 页、重庆 06 NM 8 页、湖北 06 NM 10 页，+8 是中位
    """
    for i, (name, toc_start) in enumerate(toc):
        if "新型材料" not in name:
            continue
        toc_start_real = toc_start + page_offset

        # 优先级 1: yaml 配 nm_end
        if city:
            try:
                yaml_data = load_city_yaml(city)
                nm_end_yaml = yaml_data.get("nm_end")
                if nm_end_yaml:
                    return int(nm_end_yaml)
            except FileNotFoundError:
                pass

        # 优先级 2: 经验 fallback
        nm_end_fallback = toc_start_real + 8

        # 优先级 3: 章节内表最大 page_idx（仅当 NM 不是 toc 最后一项时有效）
        if i + 1 < len(toc):
            toc_end = toc[i + 1][1] - 1
            toc_end_real = toc_end + page_offset
            max_p = 0
            for item in content_list:
                if item.get("type") != "table":
                    continue
                p = item.get("page_idx", 0)
                if toc_start_real <= p <= toc_end_real:
                    max_p = max(max_p, p)
            if max_p:
                return min(max_p, nm_end_fallback)
        return nm_end_fallback
    return None


def load_city_yaml(city):
    """读 6_配置/城市模板/{city}.yaml 或 {city}市.yaml"""
    import yaml
    candidates = [
        ROOT / "6_配置" / "城市模板" / f"{city}.yaml",
        ROOT / "6_配置" / "城市模板" / f"{city}市.yaml",
    ]
    for path in candidates:
        if path.exists():
            with path.open(encoding="utf-8") as f:
                return yaml.safe_load(f)
    raise FileNotFoundError(f"未找到城市模板: {candidates[0]} 或 {candidates[1]}")


def build_page_to_section(toc, classify_fn, page_offset=0, nm_end=None):
    """构造 page_idx → section_type 映射

    2026-07-30 北京版改：识别"主章节 vs 子章节"两级 toc 结构
      - 北京 toc 有 31 项：7 主章节 + 12 子章节编码 + 12 子条目
      - 子章节 page 跟主章节重叠（"1．黑色及有色金属" toc_p=10 = 主章节"一" toc_p=10）
      - 旧逻辑按 toc 顺序遍历，end = next_start - 1，子章节（UNKNOWN）把主章节 range 截断为空
      - 修复：
        1. 主章节（"一、二、三...七、"开头）range = [start, 下一主章节 start - 1]
        2. 子章节 section=UNKNOWN 跳过（继承最近主章节）
        3. 子章节升格（PC/NM/SKIP 有明确 keyword match）range 单独写
           end = 下一个 toc entry start - 1（不是下一个主章节）

    2026-07-28 旧版（成都/重庆仍用）：
      - page_offset 修正 TOC 页码 → 真实 page_idx
      - NM 章节 end 用 nm_end 限定
    """
    page_to_section = {}
    # 1. 识别主章节（章节名以中文数字"一二三四五六七八九十"开头）
    main_pattern = re.compile(r"^[一二三四五六七八九十]+[、，．]")
    main_indices = [i for i, (name, _) in enumerate(toc) if name != "END_OF_TOC" and main_pattern.match(name)]

    # 2. 写主章节 range
    for i, idx in enumerate(main_indices):
        name, start = toc[idx]
        section = classify_fn(name)
        if i + 1 < len(main_indices):
            next_start = toc[main_indices[i + 1]][1]
        else:
            next_start = 9999
        end_paper = next_start - 1
        start_real = start + page_offset
        if section == "NEW_MATERIAL" and nm_end is not None:
            end_real = nm_end
        else:
            end_real = end_paper + page_offset
        for p in range(start_real, end_real + 1):
            if p not in page_to_section:
                page_to_section[p] = section

    # 3. 写子章节升格段（section != UNKNOWN 的非主章节）
    for i, (name, start) in enumerate(toc):
        if name == "END_OF_TOC" or i in main_indices:
            continue
        section = classify_fn(name)
        if section == "UNKNOWN":
            continue  # 子章节未升格 → 继承父章节（已写过）
        next_start = toc[i + 1][1] if i + 1 < len(toc) else 9999
        end_paper = next_start - 1
        start_real = start + page_offset
        if section == "NEW_MATERIAL" and nm_end is not None:
            end_real = nm_end
        else:
            end_real = end_paper + page_offset
        for p in range(start_real, end_real + 1):
            if p not in page_to_section:
                page_to_section[p] = section
    return page_to_section


def classify(result_json, log_lines=None, city=None):
    """result.json → classified.json

    2026-07-28 加 city 参数（读 yaml 配 nm_end）
    """
    if log_lines is None:
        log_lines = []

    with open(result_json, encoding="utf-8") as f:
        data = json.load(f)

    inner = data["results"]["upload"]
    content_list = json.loads(inner["content_list"])
    md_content = inner["md_content"]

    skip_kw, market_kw, pc_kw, new_material_kw = load_keywords(city=city)

    # 1. 解析目录页（page_offset 先按 0 占位，1b 算完真实值后无需重 parse）
    # 2026-07-29 改：从 yaml 读 toc_format（重庆=leading，其他=trailing）
    toc_format = None
    if city:
        try:
            yaml_data = load_city_yaml(city)
            toc_format = yaml_data.get("toc_format")
        except (FileNotFoundError, ValueError):
            pass
    print(f"[step2] 解析目录页（page 0-3, toc_format={toc_format or 'auto'}）...")
    toc = parse_toc(content_list, toc_pages=4, page_offset=0, toc_format=toc_format)
    print(f"[step2] 找到 {len(toc)} 个章节（含 END_OF_TOC sentinel）:")
    for name, page in toc:
        section = classify_section_name(name, skip_kw, market_kw, pc_kw, new_material_kw)
        print(f"  '{name[:30]}' → 页{page} → {section}")
        log_lines.append(f"  [step2] TOC: '{name[:30]}' → 页{page} → {section}")

    # 1b. 自动算 page_offset（2026-07-28 增，基于"市场信息价"章节首张表反推）
    page_offset = compute_page_offset(content_list, toc, city=city)
    print(f"[step2] 自动算 page_offset = {page_offset}（TOC 页 → page_idx 偏移）")
    log_lines.append(f"[step2] page_offset = {page_offset}")

    # 1c. 自动算 NM 章节 end（2026-07-28 增，yaml 配 + 经验 fallback）
    nm_end = compute_nm_end(content_list, toc, page_offset, city=city)
    if nm_end is not None:
        print(f"[step2] NM 章节 end = page_idx {nm_end}（自动算）")
        log_lines.append(f"[step2] NM 章节 end = page_idx {nm_end}")

    # 2. 构造 page → section 映射
    classify_fn = lambda name: classify_section_name(name, skip_kw, market_kw, pc_kw, new_material_kw)
    page_to_section = build_page_to_section(
        toc, classify_fn, page_offset=page_offset, nm_end=nm_end
    )

    # 3. 给每个 table 打 section
    # 3a. caption 级 SKIP 覆盖（章节级分类可能误判：如 新型材料章节里夹 租赁/指数 表）
    table_skip_kw = [
        # 租赁/机械
        "租赁价格", "机械设备", "周转材料", "市场租赁",
        # 价格走势/指数
        "价格走势", "走势图", "趋势图", "折线图",
        "价格指数", "造价指数", "材料价格指数",
        "同比", "环比", "涨幅",
        # 厂商报价（公司名 / 报价）
        "厂商报价", "供应商报价", "企业报价",
        "有限公司", "科技公司", "建材公司", "材料公司",
    ]
    # 2026-07-28 加：caption SKIP 的"公司" kw 仅在 PC 章节外的页生效，避免误杀 PC 构件表
    # 根因：用户要求"一起修"，caption 含"公司"的 PC 构件表可能被误 SKIP
    # 修法：先记下 SKIP，循环后判断如果是 PC 章节 → 撤销 SKIP
    caption_skipped_indices = set()

    # 3b. table-level PC 检测：找前置文本里的 PC 关键词
    # 原因：TOC 说 PC 在 page 169，实际在 page 172-173（页码错位）
    # 方法（2026-07-27 改）：扫 content_list 文本项含 PC 关键词 → 该页所有 table 归 PC
    # 旧方法只覆盖后续 2 张表 → p172 房建+市政表 UNKNOWN，p173 市政 II MARKET（错），4/4 fail
    pc_table_kw = ["PC 构件", "钢构件", "预制构件", "装配式"]
    pc_table_indices = set()
    pc_pages = set()  # 含 PC 标题的页码
    for i, item in enumerate(content_list):
        if item.get("type") != "text":
            continue
        txt = item.get("text", "")
        if any(kw in txt for kw in pc_table_kw):
            pc_pages.add(item.get("page_idx", 0))
    # 含 PC 标题的页 → 该页所有 table 归 PC
    for idx, item in enumerate(content_list):
        if item.get("type") != "table":
            continue
        if item.get("page_idx", 0) in pc_pages:
            pc_table_indices.add(idx)

    n_market = n_pc = n_skip = n_unknown = n_new_material = n_caption_skip = n_pc_override = 0
    for idx, item in enumerate(content_list):
        if item.get("type") != "table":
            continue
        page = item.get("page_idx", 0)
        section = page_to_section.get(page, "UNKNOWN")

        # caption 级 SKIP 覆盖
        cap = item.get("table_caption", [])
        cap_text = cap[0] if (isinstance(cap, list) and cap) else (cap if isinstance(cap, str) else "")
        for kw in table_skip_kw:
            if kw in cap_text:
                section = "SKIP"
                n_caption_skip += 1
                break

        # 2026-07-28 删：page > 195 硬编码（已用 build_page_to_section 的 nm_end 自动算替代）
        # 之前迭代史：
        #   page > 178 → 179-180 三表错杀 → 改 page > 180
        #   page > 180 → 181 国塑 HDPE-PG 错杀 → 改 page > 181
        #   page > 181 → 182-195 厂商报价表误归 NM（用户澄清：厂商报价 ≠ 新型材料）
        # 现在：NM 章节 end 用 compute_nm_end(content_list, toc, page_offset) 自动算
        #       page_offset 用 compute_page_offset(content_list, toc) 自动算
        #       page_idx 范围严格按 TOC 标注（不再硬编码）

        # PC 文本前置 → 强制 PC（必须在 SKIP 之前覆盖，否则 PC 章节的表都被 SKIP 走了）
        if idx in pc_table_indices:
            section = "PC"
            n_pc_override += 1

        item["section"] = section
        if section == "MARKET":
            n_market += 1
        elif section == "PC":
            n_pc += 1
        elif section == "NEW_MATERIAL":
            n_new_material += 1
        elif section == "SKIP":
            n_skip += 1
        else:
            n_unknown += 1

    print(f"[step2] 分类: MARKET={n_market} | PC={n_pc} | NEW_MATERIAL={n_new_material} | SKIP={n_skip} | UNKNOWN={n_unknown}")
    if n_caption_skip:
        print(f"[step2]   其中 caption 级 SKIP 覆盖: {n_caption_skip}")
    if n_pc_override:
        print(f"[step2]   其中 PC 文本前置覆盖: {n_pc_override}")
    if nm_end is not None:
        # 找 NM 章节 TOC 起始页
        nm_toc_start = next((p for n, p in toc if "新型材料" in n), 0)
        nm_start_real = nm_toc_start + page_offset
        print(f"[step2]   NM 章节严格范围: page_idx {nm_start_real} - {nm_end}（page_offset={page_offset}, NM 章节表 {nm_end - nm_start_real + 1} 页）")

    # 4. 写 classified.json
    out = {
        "classified_at": datetime.now().isoformat(),
        "toc": toc,
        "page_to_section": page_to_section,
        "content_list": content_list,
        "md_content": md_content,
    }
    out_path = Path(result_json).parent / "classified.json"
    out_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[step2] 写出: {out_path}")

    # 5. 写 page_audit.json（2026-07-29 增：方案 C 混合 - 自动报表 + 人工 spot check）
    audit_path = Path(result_json).parent / "page_audit.json"
    audit = {
        "generated_at": datetime.now().isoformat(),
        "page_offset": page_offset,
        "nm_end": nm_end,
        "toc": [(name, page) for name, page in toc],
        "boundaries": [],   # 候选边界点
        "section_summary": {},  # section → page_idx 列表
        "table_count": {
            "MARKET": n_market,
            "PC": n_pc,
            "NEW_MATERIAL": n_new_material,
            "SKIP": n_skip,
            "UNKNOWN": n_unknown,
        },
        "spot_check_targets": [
            # 2026-07-29 改：人工 spot check 5 个分界点
            {"name": "MARKET 首张数据表（综合价格信息首章）", "expected_page": 62, "tolerance": 1},
            {"name": "MARKET 苗木表（独立 sheet）", "expected_page": 91, "tolerance": 1},
            {"name": "MARKET 区县汇总（区县切换）", "expected_page": 99, "tolerance": 1},
            {"name": "NM 段首章（行业协会首张）", "expected_page": 120, "tolerance": 1},
            {"name": "NM 段末章（最后行业协会）", "expected_page": 127, "tolerance": 1},
        ],
    }
    # 候选边界点：每张表给 toc 章节起始 vs 实际 page_idx
    seen_chapters = set()
    for item in content_list:
        if item.get("type") != "table":
            continue
        sec = item.get("section", "UNKNOWN")
        page = item.get("page_idx", 0)
        if sec not in audit["section_summary"]:
            audit["section_summary"][sec] = []
        audit["section_summary"][sec].append(page)
    # 找每章节首张表（边界点）
    for i, (name, toc_start) in enumerate(toc):
        if name == "END_OF_TOC":
            continue
        real_start = toc_start + page_offset
        # 找该章节首张 section==当前 的表
        first_table = None
        for item in content_list:
            if item.get("type") != "table":
                continue
            if item.get("section", "") == classify_section_name(
                name, skip_kw, market_kw, pc_kw, new_material_kw
            ):
                p = item.get("page_idx", 0)
                if first_table is None or p < first_table:
                    first_table = p
        if first_table is not None:
            audit["boundaries"].append({
                "chapter_name": name[:40],
                "toc_start": toc_start,
                "expected_page_idx": real_start,
                "actual_first_table_page_idx": first_table,
                "match": first_table == real_start,
            })
    # 写 page_audit.json
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[step2] 📋 写出 page_audit: {audit_path}")
    print(f"[step2] 📋 边界点 {len(audit['boundaries'])} 个（人工 spot check 5 个分界点）")

    return out_path, log_lines


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python step2_classify.py <result_json_path> [city]", file=sys.stderr)
        sys.exit(1)
    city = sys.argv[2] if len(sys.argv) > 2 else None
    classify(sys.argv[1], city=city)
