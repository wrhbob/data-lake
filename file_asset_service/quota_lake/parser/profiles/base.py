"""BaseProfile — 声明式省级差异, 纯数据 Dataclass."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BaseProfile:
    """各省解析 Profile 的基类, 包含所有可重载参数。

    子类 (如 SichuanProfile2020) 只需赋具体值;
    解析器 (table_parser / page_classifier) 通过 profile 参数获取省际差异。
    """

    # ── 编号 ──
    code_pattern: str = r"^[A-Z]{2}\d{4}$"
    """定额编号正则, 如 AA0001."""

    # ── 章节 ──
    chapter_pattern: str = r"^([A-Z]\.\d+(?:\.\d+)*)\s*(\S.*?)\s*(?:[((]\s*编\s*码\s*[::]\s*(\d{6,9})\s*[))])?\s*$"
    """章节标题匹配."""

    # ── 费用类型 ──
    cost_scope: str = "comprehensive"
    """comprehensive(综合基价) | direct(直接费) | other."""

    cost_rows: list = field(default_factory=lambda: ["综合基价", "人工费", "材料费", "机械费"])
    """基价行标签列表."""

    # ── 编号行检测 ──
    code_header_keywords: list = field(default_factory=lambda: ["定额编号", "编号"])
    """编号行表头关键词."""

    # ── 单位 ──
    unit_position: str = "table_header_right"
    """单位标注位置: table_header_right | inline | etc."""

    # ── 资源类别 ──
    resource_categories: list = field(default_factory=lambda: ["人工", "材料", "机械"])
    """消耗矩阵中的类别关键词."""

    # ── 水印 ──
    watermark_chars: set = field(default_factory=set)
    """水印字符集, parser 清洗时过滤."""

    # ── 表格结构 ──
    price_row_labels: list = field(default_factory=lambda: ["基价", "人工费", "材料费", "机械费"])
    """费用行的标签 (用于行分类游标)."""

    name_section_header: str = "项目"
    """名称区表头关键词 (会被 strip)."""
