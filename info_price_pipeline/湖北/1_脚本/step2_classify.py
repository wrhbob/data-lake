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


def load_keywords():
    """加载 SKIP/MARKET/PC/NEW_MATERIAL 关键词（2026-07-28 拆 NM 出独立 section）"""
    skip = load_config("skip_keywords.json")["categories"]
    sections = load_config("section_titles.json")

    skip_kw = set()
    for v in skip.values():
        skip_kw.update(v)

    market_kw = set()
    market_kw.update(sections["MARKET_MATERIAL"]["kw"])
    # 2026-07-31 改：MARKET_REGENERATED（再生资源材料）归 MARKET 类
    # 成都 02 有此章节（页 129），成都 06 没这章（不命中关键词）
    if "MARKET_REGENERATED" in sections:
        market_kw.update(sections["MARKET_REGENERATED"]["kw"])
    # 2026-07-31 增：MARKET_REFERENCE（参考价专栏）归 MARKET 类
    if "MARKET_REFERENCE" in sections:
        market_kw.update(sections["MARKET_REFERENCE"]["kw"])
    # 2026-07-31 改：MARKET_LABOR（人工成本）改回 SKIP（不入 xlsx，等以后单独处理）
    # 原因：人工成本表 schema 跟材料表不同（无规格/单位/价格），归 MARKET 会污染数据
    # 加 SKIP 关键词（不直接用 MARKET_LABOR，避免被解析为 MARKET）
    labor_kw = set(sections.get("MARKET_LABOR", {}).get("kw", []))
    skip_kw.update(labor_kw)
    # 2026-07-31 改：MARKET_RENTAL（机械租赁）改回 SKIP（不入 xlsx，等以后单独处理）
    # 原因：用户原话「机械租赁价 ✅ 沾边——是材料价格延伸 这两个也不用」
    rental_kw = set(sections.get("MARKET_RENTAL", {}).get("kw", []))
    skip_kw.update(rental_kw)
    # MARKET_NEW_MATERIAL 已拆出独立章节（2026-07-28 改）

    pc_kw = set(sections["MARKET_PC"]["kw"])
    new_material_kw = set(sections["MARKET_NEW_MATERIAL"]["kw"])

    return skip_kw, market_kw, pc_kw, new_material_kw


def is_toc_page(texts):
    """判断这页是不是目录页（不靠硬编码页数，靠特征识别）

    2026-07-31 增：is_toc_page 算法（替代 toc_pages=4 硬编码）
    特征：
      1. 大量行尾 "... (数字)" 或 "...（数字）" 格式（≥ 30% 行）
      2. 含 "目录" 章节标题关键字
      3. 行数 > 8 且全是短行（标题格式，每行 < 50 字）

    参数：texts = list of str（一页的所有 text 项 text 字段）
    返回：bool
    """
    if not texts:
        return False
    # 特征 1：行尾 ...(数字) 格式占比
    toc_line_pattern = re.compile(r"[…\.]{2,}\s*[（(]\s*\d+\s*[）)]?")
    toc_line_count = sum(1 for t in texts if toc_line_pattern.search(t))
    if toc_line_count >= max(3, len(texts) * 0.3):
        return True
    # 特征 2：含 "目录" 字样
    if any("目录" in t for t in texts) and len(texts) > 5:
        return True
    # 特征 3：全是短行（标题格式）
    if len(texts) > 8 and all(len(t) < 50 for t in texts):
        return True
    return False


def parse_toc(content_list, toc_pages=99, page_offset=0):
    """扫目录页找 (章节名, 起始页码) 列表

    2026-07-31 改：用 is_toc_page() 特征识别替代 toc_pages=4 硬编码
      - 旧逻辑：只扫 page 0-3，漏掉成都 02 这类 p4 才是目录的 PDF
      - 新逻辑：扫所有 page_idx，但只从 is_toc_page() 识别的目录页抽章节
      - toc_pages 参数保留兼容（默认 99 = 不限制）

    正则: "章节名 ..." + "(页码)" 或 "章节名 ... (页码)"
    例: "市场信息价格 …… (50)" → ("市场信息价格", 50)

    2026-07-28 改：末尾补 END_OF_TOC sentinel
    """
    toc = []
    pattern = re.compile(r"^(.+?)\s*[…\.]{2,}\s*[\(（]\s*(\d+)\s*[\)）]?")

    # 1. 先按页聚合 text（每页所有 text 项合并判断）
    pages = {}
    for item in content_list:
        if item.get("type") != "text":
            continue
        page = item.get("page_idx", 0)
        if page >= toc_pages:
            continue
        text = item.get("text", "").strip()
        if page not in pages:
            pages[page] = []
        pages[page].append(text)

    # 2. 识别目录页（is_toc_page 特征）
    toc_page_indices = sorted([p for p, texts in pages.items() if is_toc_page(texts)])
    if not toc_page_indices:
        # 兜底：如果没有任何页被识别为目录，按 page 0-3 旧逻辑
        toc_page_indices = sorted([p for p in pages.keys() if p < 4])

    # 3. 从目录页抽章节
    for page in toc_page_indices:
        for text in pages[page]:
            m = pattern.match(text)
            if m:
                name = m.group(1).strip()
                start_page = int(m.group(2))
                toc.append((name, start_page))

    # 2026-07-29 增：lead_num fallback（重庆期刊式 TOC，例："60 综合价格信息"）
    if not toc:
        lead_pattern = re.compile(r"^(\d+)\s+(.+)")
        for page in toc_page_indices:
            for text in pages[page]:
                m = lead_pattern.match(text)
                if m:
                    start_page = int(m.group(1))
                    name = m.group(2).strip()
                    if start_page >= 10:
                        toc.append((name, start_page))
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


def compute_page_offset(content_list, toc):
    """基于 PC 锚点反推 page_offset（2026-08-03 重写）

    关键洞察（用户决策 2026-08-03）：
      - PC 章节表头最稳定（"构件名称" + "到场价"）
      - 不投票不多数派，PC 锚点优先
      - PC 不存在 → 退到 MARKET 锚点
      - 都不存在 → yaml 兜底

    为什么不用多数派：
      - 2025-7 / 2026-3 实际 offset=+2，但 MARKET 章节 (50 页) 范围广，
        里面"前导页" (价格指数/趋势图) 拖 offset 成 +3
      - 多数派机制没把 MARKET 章节"前 2 页是趋势表"这个特征排除
      - 4 期实测：
          2023-5: PC=131 toc=129 → +2  (对)
          2025-7: PC=155 toc=153 → +2  (对，MARKET 算 +3 是错)
          2024-8: PC=150 toc=147 → +3  (对)
          2026-3: PC=186 toc=184 → +2  (对，MARKET 算 +3 是错)
    """
    skip_kw_yaml, market_kw, pc_kw, new_material_kw = load_keywords()

    # 优先 1: PC 锚点（"构件名称" + "到场价" 特征）
    pc_anchor_p = _find_pc_anchor(content_list)
    toc_pc = _find_chapter_by_kw(toc, pc_kw)
    if pc_anchor_p is not None and toc_pc is not None:
        toc_pc_name, toc_pc_start = toc_pc
        offset = pc_anchor_p - toc_pc_start
        print(f"[step2] PC 锚点 page_idx={pc_anchor_p}, toc={toc_pc_name} 印刷页 {toc_pc_start} → offset={offset}")
        return offset

    # 优先 2: MARKET 锚点（"材料名称" + "规格型号" + "产地" 特征）
    market_anchor_p = _find_market_anchor(content_list)
    toc_market = _find_chapter_by_kw(toc, market_kw)
    if market_anchor_p is not None and toc_market is not None:
        toc_market_name, toc_market_start = toc_market
        offset = market_anchor_p - toc_market_start
        print(f"[step2] PC 没找到，回退 MARKET 锚点 page_idx={market_anchor_p}, toc={toc_market_name} 印刷页 {toc_market_start} → offset={offset}")
        return offset

    # 兜底
    print(f"[step2] ⚠️ PC 和 MARKET 锚点都没找到, fallback=3")
    return 3


def _find_chapter_by_kw(toc, kw_set):
    """找 toc 中第一个匹配 kw_set 的章节"""
    for name, toc_start in toc:
        if name == "END_OF_TOC":
            continue
        if any(kw in name for kw in kw_set):
            return (name, toc_start)
    return None


def _find_pc_anchor(content_list):
    """找首张含「构件名称 + 到场价」的表（PC 章节特征）

    验证（2026-08-03 4 期实跑）：
      2023-5: page_idx=131 ✓
      2025-7: page_idx=155 ✓
      2024-8: page_idx=150 ✓
      2026-3: page_idx=186 ✓
    """
    import re
    for item in content_list:
        if item.get("type") != "table":
            continue
        body = item.get("table_body", "") or ""
        text = re.sub(r"<[^>]+>", " ", body)
        if "构件名称" in text and "到场价" in text:
            return item.get("page_idx", 0)
    return None


def _find_market_anchor(content_list):
    """找首张含「材料名称 + 规格型号 + 产地」的表（MARKET 章节特征）"""
    import re
    for item in content_list:
        if item.get("type") != "table":
            continue
        body = item.get("table_body", "") or ""
        tds = re.findall(r"<td[^>]*>([^<]+)</td>", body)
        head = tds[:5] if tds else []
        if ("材料名称" in head and "规格型号" in head and "产地" in head):
            return item.get("page_idx", 0)
    return None


def _find_nm_anchor(content_list, pc_anchor_p):
    """找 PC 锚点之后的 NM 章节首张表

    验证（2026-08-03 4 期实跑）：
      2023-5: PC=131 后 NM=135 (侧向支架)
      2025-7: PC=155 后 NM=163 (侧向支架)
      2024-8: PC=150 后 NM=154 (侧向支架)
      2026-3: PC=186 后 NM=192 (DRPO)

    优先级：
      1. 抗震支架/侧向支架（NM 章节序言表，2023-5/2025-7/2024-8 用）
      2. 建华建材 + 表头含「品牌」 + 4 列（NM 章节典型表）
      3. DRPO / HDPE / 改性聚烯烃（NM 章节特殊表，2026-3 用）
    """
    import re
    for item in content_list:
        if item.get("type") != "table":
            continue
        p = item.get("page_idx", 0)
        if pc_anchor_p is not None and p <= pc_anchor_p:
            continue
        body = item.get("table_body", "") or ""
        text = re.sub(r"<[^>]+>", " ", body)
        tds = re.findall(r"<td[^>]*>([^<]+)</td>", body)
        head = tds[:5] if tds else []

        # 优先级 1: 抗震支架 / 侧向支架
        if "抗震支架" in text or "侧向支架" in text:
            return p

        # 优先级 2: 「品牌」+「单位」+「材料/产品」+ 建华建材/协会推荐/DRPO
        if ("品牌" in head and "单位" in head and any(k in head for k in ["材料名称", "产品名", "产品名称"])):
            if any(s in text for s in ["建华建材", "协会推荐", "DRPO", "HDPE", "改性聚烯烃", "新型建材", "我协会"]):
                return p

        # 优先级 3: DRPO / HDPE / 改性聚烯烃
        if any(s in text for s in ["DRPO", "HDPE", "改性聚烯烃"]):
            return p

    return None


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
            # 2026-07-31 增：空 table（无 <tr>）也跳过
            # 根因：2023-5 MARKET toc=87 范围内 page_idx=86 是空 table 占位，
            #       原代码 None 时不 continue → 取 page_idx=86 当 first → offset=-1
            if not first_tr_match:
                continue
            td_count = len(re.findall(r"<td", first_tr_match.group(1)))
            if td_count < min_cols:
                continue
        p = item.get("page_idx", 0)
        if toc_start - 1 <= p <= toc_start + page_window:
            if first_p is None or p < first_p:
                first_p = p
    return first_p


def compute_nm_end(content_list, toc, page_offset, city=None, period=None):
    """NM 章节实际 end = yaml 按期配 或 自动算

    2026-07-31 改：恢复自动 fallback（用户原话"02 不需要配YAML，只需要根据成都的模板照着样子提取"）
      - 优先级 1: yaml `nm_end.{period}` 配了 → 用该值
      - 优先级 2: 自动找"最后一张含 NM caption 的 table"
        （解决：之前用"NM 章节内最大 page_idx"会把厂商报价表也算进来）

    算法（2026-07-31 改）：
      1. 找 toc_start_real 到 toc_next_start_real - 1 之间所有 table
      2. 过滤：table_caption 包含 NM 关键字（新型/再生/协会/推荐/HDPE 等）
      3. 取最大 page_idx 作为 nm_end
      4. 兜底：没找到 → 用 toc_start_real + 12（NM 章节典型跨度）

    旧 bug：成都 06 page 182-223 厂商报价表（caption 空）被算进 NM，
            导致 build_page_to_section 把 NM 扩展到 220，污染 39 页空白
    """
    # 2026-07-31 改 v2：用「第一张 vendor 表」作为 NM 结束标志
    # 旧算法（依赖 NM kw 命中）失败根因：
    #   成都 02 真 NM 表 80+ 张，caption 大部分空（不命中 NM kw）
    #   page 213 caption 含"材料价格"+"参考"误命中 → max_p=213 → NM 82 页跨度
    #   加 vendor_exclude 后：max_p=0 → 兜底 toc_start+12 → NM 12 页（145-183 全 SKIP）
    # 新算法（不依赖 NM kw 命中）：
    #   找 toc_start_real 之后**第一张 vendor 表**（caption 含 报价/公司/厂家/经销商/声明）
    #   vendor 表所在 page - 1 = NM 结束
    #   没找到 → 兜底 toc_next_start_real - 1 或 toc_start_real + 12
    # 验证：02 期 page 184 "报价单" 是第一张 vendor → NM end = 183 ✓（真 NM 范围 133-183）
    sections = load_config("section_titles.json")
    # vendor caption 特征（厂商报价章节）
    vendor_kw = ["报价", "公司", "厂家", "经销商", "声明"]

    for i, (name, toc_start) in enumerate(toc):
        if "新型材料" not in name:
            continue

        toc_start_real = toc_start + page_offset

        # 优先级 1: yaml 按期配 nm_end
        if city and period:
            try:
                yaml_data = load_city_yaml(city)
                nm_end_map = yaml_data.get("nm_end", {})
                if period in nm_end_map:
                    return int(nm_end_map[period])
            except FileNotFoundError:
                pass

        toc_next_start_real = None
        if i + 1 < len(toc):
            toc_next_start_real = toc[i + 1][1] + page_offset

        # 优先级 2 (2026-08-03): 用 NM 锚点（PC 锚点之后 + 抗震支架/DRPO 等）作为起点
        # 然后找 NM 锚点之后**第一张 vendor 表**（公司简介 / 报价单 / 有限公司）
        # 防止：NM 章节范围延伸到厂商报价章节
        # 2026-08-03 用户要求：NM 边界严格，不固定的 +N
        # pc_anchor_p: 用 PC 锚点反推（找 toc 中 PC 章节）
        skip_kw_yaml, market_kw, pc_kw, new_material_kw = load_keywords()
        toc_pc = _find_chapter_by_kw(toc, pc_kw)
        pc_anchor_p = _find_pc_anchor(content_list)
        nm_anchor_p = _find_nm_anchor(content_list, pc_anchor_p)

        # 决定 NM 起点（toc_start_real vs nm_anchor_p，哪个更靠后）
        # 用 nm_anchor_p 为准（包括能跳过 MARKET 续表）
        nm_start_real = nm_anchor_p if nm_anchor_p is not None else toc_start_real

        # 找 nm_start_real 之后**第一张 vendor 表**（真实厂商报价单，非 NM 协会推荐表）
        # 限定范围：不超过 toc_next_start_real - 1
        # 2026-08-03 修：caption 单独含"厂商报价"不够（NM 协会推荐表也用"抗震支架厂商报价"作小标题）
        # 必须 caption + body 复合判断：caption 含 vendor 关键词 AND body 含≥1 个强 vendor 特征
        # 强 vendor 特征：公司简介 / 报价单（高概率仅在真 vendor 表）
        first_vendor_p = None
        vendor_in_text_kw = ["公司简介", "报价单"]
        for item in content_list:
            if item.get("type") != "table":
                continue
            cap = item.get("table_caption", [])
            cap_text = cap[0] if isinstance(cap, list) and cap else (cap if isinstance(cap, str) else "")
            # 条件 1: caption 含 vendor_kw（hint 候选）
            if not any(kw in cap_text for kw in vendor_kw):
                continue
            p = item.get("page_idx", 0)
            if p < nm_start_real:
                continue
            if toc_next_start_real is not None and p >= toc_next_start_real:
                continue
            # 条件 2: body 含强 vendor 特征（公司简介 / 报价单）
            # 排除 NM 协会推荐表（caption 含"厂商报价"但 body 无公司简介/报价单）
            body = item.get("table_body", "") or ""
            import re as _re
            text = _re.sub(r"<[^>]+>", " ", body)
            if not any(kw in text for kw in vendor_in_text_kw):
                continue
            if first_vendor_p is None or p < first_vendor_p:
                first_vendor_p = p

        # 优先级 2-补: caption 不含，但**正文文本**含「公司简介」或「报价单」
        # 解决: 2023-5 page 198 "四川卓邦安新材料科技有限公司" 在 table_body 文本里
        # 2026-08-03 改：建华建材公司在 NM 章节内出现 → 必须 ≥2 个 vendor 关键词 OR "报价单" 在表头
        if first_vendor_p is None:
            import re
            for item in content_list:
                if item.get("type") != "table":
                    continue
                p = item.get("page_idx", 0)
                if p < nm_start_real:
                    continue
                if toc_next_start_real is not None and p >= toc_next_start_real:
                    continue
                body = item.get("table_body", "") or ""
                text = re.sub(r"<[^>]+>", " ", body)
                hit_kws = [kw for kw in vendor_in_text_kw if kw in text]
                # 至少 2 个 vendor 关键词 OR 「报价单」单独出现也算
                if len(hit_kws) >= 2 or "报价单" in text:
                    if first_vendor_p is None or p < first_vendor_p:
                        first_vendor_p = p

        if first_vendor_p is not None:
            print(f"[step2] NM 锚点 page_idx={nm_anchor_p}, 厂商报价首张 page_idx={first_vendor_p} → NM end={first_vendor_p - 1}")
            return first_vendor_p - 1

        # 兜底：没找到 vendor 表 → toc_next_start_real - 1 或 toc_start_real + 12
        if toc_next_start_real is not None:
            return toc_next_start_real - 1
        return toc_start_real + 12
    return None


def load_city_yaml(city):
    """读 6_配置/城市模板/{city}.yaml 或 {city}市.yaml

    2026-07-31 增：读后调 validate_city_yaml() 校验 schema，不通过立即报错
    """
    import yaml
    from utils import validate_city_yaml  # P1.4 schema 校验
    candidates = [
        ROOT / "6_配置" / "城市模板" / f"{city}.yaml",
        ROOT / "6_配置" / "城市模板" / f"{city}市.yaml",
    ]
    for path in candidates:
        if path.exists():
            with path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
            validate_city_yaml(data, city_name=city)  # P1.4 校验
            return data
    raise FileNotFoundError(f"未找到城市模板: {candidates[0]} 或 {candidates[1]}")


def build_page_to_section(toc, classify_fn, page_offset=0, nm_end=None):
    """构造 page_idx → section_type 映射

    2026-07-28 改：
      - page_offset 修正 TOC 页码 → 真实 page_idx（不同城市偏移不同）
      - NM 章节 end 用 nm_end 限定（自动算，非 9999）
        防 NM 章节范围延伸到后续"厂商报价"章节

    注意：nm_end 已是真实 page_idx（含 page_offset），build 时直接用，不再加 page_offset
    """
    page_to_section = {}
    for i, (name, start) in enumerate(toc):
        # 2026-07-28 改：删 9999 sentinel 分支（parse_toc 末尾必补 END_OF_TOC）
        # 单位统一：toc 都是印刷页，build 时统一加 page_offset
        end_paper = toc[i + 1][1] - 1 if i + 1 < len(toc) else 9998  # 末尾 sentinel 兜底
        section = classify_fn(name)
        # page_offset 修正（toc → 真实 page_idx）
        start_real = start + page_offset
        if section == "NEW_MATERIAL" and nm_end is not None:
            end_real = nm_end  # nm_end 已是真实 page_idx，不再加 page_offset
        else:
            end_real = end_paper + page_offset
        # 跳过 END_OF_TOC sentinel（最后一项 name = END_OF_TOC）
        if name == "END_OF_TOC":
            continue
        for p in range(start_real, end_real + 1):
            if p not in page_to_section:
                page_to_section[p] = section
    return page_to_section


def classify(result_json, log_lines=None, city=None, period=None):
    """result.json → classified.json

    2026-07-28 加 city 参数（读 yaml 配 nm_end）
    2026-07-29 加 period 参数（按期查 nm_end，未配报错）
    """
    if log_lines is None:
        log_lines = []

    with open(result_json, encoding="utf-8") as f:
        data = json.load(f)

    inner = data["results"]["upload"]
    content_list = json.loads(inner["content_list"])
    md_content = inner["md_content"]

    skip_kw, market_kw, pc_kw, new_material_kw = load_keywords()

    # 1. 解析目录页（page_offset 先按 0 占位，1b 算完真实值后无需重 parse）
    # 2026-07-31 改：用 is_toc_page() 特征识别目录页，不再传 toc_pages=4
    print("[step2] 解析目录页（is_toc_page 特征识别）...")
    toc = parse_toc(content_list, toc_pages=99, page_offset=0)
    print(f"[step2] 找到 {len(toc)} 个章节（含 END_OF_TOC sentinel）:")

    # 2026-07-31 增：收集 UNKNOWN 章节（脚本不依赖 AI，必须给用户明确反馈）
    # 原因：section_titles.json 词库未覆盖的章节分类不了 → 用户必须知道要加哪些词
    unknown_chapters = []  # [{name, toc_page, suggest}]
    classify_fn = lambda name: classify_section_name(name, skip_kw, market_kw, pc_kw, new_material_kw)
    for name, page in toc:
        if name == "END_OF_TOC":
            continue
        section = classify_fn(name)
        print(f"  '{name[:30]}' → 页{page} → {section}")
        log_lines.append(f"  [step2] TOC: '{name[:30]}' → 页{page} → {section}")
        if section == "UNKNOWN":
            # 启发式建议（不依赖 AI，纯字符串规则）：
            #  - 含"指数/走势" → 建议 SKIP
            #  - 含"价格/材料/市场" → 建议 MARKET
            #  - 含"PC/构件/装配" → 建议 PC
            #  - 含"新型/再生/协会/行业" → 建议 NEW_MATERIAL
            if any(kw in name for kw in ["指数", "走势", "趋势", "涨幅"]):
                suggest = "SKIP"
            elif any(kw in name for kw in ["PC", "构件", "装配", "预制"]):
                suggest = "PC"
            elif any(kw in name for kw in ["新型", "再生", "协会", "行业推荐", "推荐"]):
                suggest = "NEW_MATERIAL"
            elif any(kw in name for kw in ["价格", "材料", "市场", "建材", "租赁", "机械"]):
                suggest = "MARKET"
            else:
                suggest = "?"
            unknown_chapters.append({
                "name": name,
                "toc_page": page,
                "suggest": suggest,
            })

    # 1b. 自动算 page_offset（2026-07-28 增，基于"市场信息价"章节首张表反推）
    page_offset = compute_page_offset(content_list, toc)
    print(f"[step2] 自动算 page_offset = {page_offset}（TOC 页 → page_idx 偏移）")
    log_lines.append(f"[step2] page_offset = {page_offset}")

    # 1c. 自动算 NM 章节 end（2026-07-28 增，yaml 配 + 经验 fallback）
    # 2026-07-29 改：删 fallback，按期查 yaml，未配报错
    nm_end = compute_nm_end(content_list, toc, page_offset, city=city, period=period)
    if nm_end is not None:
        print(f"[step2] NM 章节 end = page_idx {nm_end}（自动算）")
        log_lines.append(f"[step2] NM 章节 end = page_idx {nm_end}")

    # 2. 构造 page → section 映射（classify_fn 已在 step 1 段定义，复用）
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
        # 2026-07-31 增：PC 不含税价格表 OCR 横向识别失败（caption 含「X 不含税价格表」）
        # 根因：02 期 p124-127 的 PC 章节夹了 7 张「不含税价格表」横向子表
        #       （砌筑砂浆 / HRB400E螺纹钢筋 / 水泥 / 商品混凝土 / 元石 / 中砂 / 碎石）
        #       每张都是「2 列产品 + 12 列价格（1月-12月）」的横向矩阵
        #       OCR 把每行「到场价(含60KM...」拆成 4-5 个 cell，
        #       step3 强行套 9 列 PC schema → 几百行错位数据
        # 修法：caption 含「X 不含税价格表」/「X 价格表」→ SKIP
        # 验证：02 期这 7 张表 02 12:47 输出 Sheet 2 行 62-130 全是错位的 "产品名+3692.92" 之类的污染
        "砂浆不含税价格表", "砂浆价格表",
        "HRB400E螺纹钢筋不含税价格表",
        "水泥不含税价格表", "商品混凝土不含税价格表",
        "元石不含税价格表", "中砂不含税价格表", "碎石不含税价格表",
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

        # 2026-08-03 增：schema header 含「编者说明」+「租赁」→ 强制 SKIP
        # 根因：2023-5 p133 / 2024-8 p152 / 2025-7 p157 这张表 schema 是「编者说明:机械设备和周转材料租赁价格为含税市场信息价」
        #       caption 空 → caption_skip_kw 不命中 → step3 误套 PC 9 列 schema → 抽出 129 行机械租赁数据（洒水车/旋挖机/月租价）进 PC sheet
        #       实际是 MARKET 章节机械租赁总表，应 SKIP
        # 修法：schema header（<tr> 第一行）含「编者说明」+「租赁」→ SKIP
        # 碰撞：5 期只在 2023-5 p133 / 2024-8 p152 / 2025-7 p157 命中，2026-3/2026-06 无
        if section != "SKIP":
            body_check = item.get("table_body", "") or ""
            first_tr_match = re.search(r"<tr>(.*?)</tr>", body_check, re.DOTALL)
            if first_tr_match:
                schema_text = first_tr_match.group(1)
                if "编者说明" in schema_text and "租赁" in schema_text:
                    section = "SKIP"
                    n_caption_skip += 1

        # 2026-07-31 增：NM 范围外的 UNKNOWN table → 强制 SKIP（厂商报价章节兜底）
        # 根因：TOC 没"厂商报价"章节，nm_end 截断后 182-223 没章节归属 → UNKNOWN
        # 特征：这些 table caption 空 + 公司名（友翔/Talida/特丽达 等）
        # 修法：page > nm_end 且 section 还是 UNKNOWN → SKIP
        if section == "UNKNOWN" and nm_end is not None and page > nm_end:
            section = "SKIP"
            n_caption_skip += 1

        # 2026-07-28 删：page > 195 硬编码（已用 build_page_to_section 的 nm_end 自动算替代）
        # 之前迭代史：
        #   page > 178 → 179-180 三表错杀 → 改 page > 180
        #   page > 180 → 181 国塑 HDPE-PG 错杀 → 改 page > 181
        #   page > 181 → 182-195 厂商报价表误归 NM（用户澄清：厂商报价 ≠ 新型材料）
        # 现在：NM 章节 end 用 compute_nm_end(content_list, toc, page_offset) 自动算
        #       page_offset 用 compute_page_offset(content_list, toc) 自动算
        #       page_idx 范围严格按 TOC 标注（不再硬编码）

        # PC 文本前置 → 强制 PC（必须在 SKIP 之前覆盖，否则 PC 章节的表都被 SKIP 走了）
        # 2026-08-03 增：NM 范围外（page > nm_end）的 PC 文本前置不生效，强制 SKIP
        # 根因：2023-5 p218/219 含"装配式"文本 → 触发 PC 文本前置 → 千华/金达隆错分 PC
        #       实际这些页是 NM 章节末段厂商表（千华新材料 CEC 装配式轻质混凝土板）
        #       nm_end=197 时 p218/219 > 197 → 应 SKIP 而非 PC
        if idx in pc_table_indices and (nm_end is None or page <= nm_end):
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

    # 2026-07-31 增：UNKNOWN 章节立即反馈（脚本不依赖 AI，必须告诉用户怎么修）
    if unknown_chapters:
        print(f"\n[step2] ⚠️  UNKNOWN 章节 {len(unknown_chapters)} 个，section_titles.json 词库未覆盖：")
        for ch in unknown_chapters:
            print(f"  - '{ch['name']}' (TOC页{ch['toc_page']}) → 建议: {ch['suggest']}")
        print(f"[step2] 修复方法：")
        print(f"  1. 看 '建议' 列（启发式，非 AI 判断）")
        print(f"  2. 改 6_配置/section_titles.json，把关键词加到对应类（如 MARKET_MATERIAL.kw）")
        print(f"  3. 加 SKIP 类章节 → 改 skip_keywords.json")
        print(f"  4. 重跑 run.py\n")
        log_lines.append(f"[step2] ⚠️  UNKNOWN 章节 {len(unknown_chapters)} 个：")
        for ch in unknown_chapters:
            log_lines.append(f"  - '{ch['name']}' (TOC页{ch['toc_page']}) → 建议: {ch['suggest']}")

    # 4. 写 classified.json
    out = {
        "classified_at": datetime.now().isoformat(),
        "toc": toc,
        "page_to_section": page_to_section,
        "content_list": content_list,
        "md_content": md_content,
        "unknown_chapters": unknown_chapters,  # 2026-07-31 增：UNKNOWN 章节列表（给 step5 反馈给用户）
    }
    out_path = Path(result_json).parent / "classified.json"
    out_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[step2] 写出: {out_path}")

    return out_path, log_lines


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python step2_classify.py <result_json_path> [city]", file=sys.stderr)
        sys.exit(1)
    city = sys.argv[2] if len(sys.argv) > 2 else None
    classify(sys.argv[1], city=city)
