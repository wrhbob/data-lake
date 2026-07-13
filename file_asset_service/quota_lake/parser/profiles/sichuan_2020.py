"""SichuanProfile2020 — 四川省 2020 定额解析 Profile."""

from quota_lake.parser.profiles.base import BaseProfile


class SichuanProfile2020(BaseProfile):
    """四川省建设工程工程量清单计价定额 (房屋建筑工程) 2020 版.

    基于全链路技术规格 §3.2 + 附录 B 的具体值。
    """

    code_pattern: str = r"^[A-Z]{2}\d{4}$"
    chapter_pattern: str = (
        r"^([A-Z]\.\d+(?:\.\d+)*)\s*(\S.*?)\s*(?:[((]\s*编\s*码\s*[::]\s*(\d{6,9})\s*[))])?\s*$"
    )
    cost_scope: str = "comprehensive"
    cost_rows: list = ["综合基价", "人工费", "材料费", "机械费"]
    code_header_keywords: list = ["定额编号", "编号"]
    unit_position: str = "table_header_right"
    resource_categories: list = ["人工", "材料", "机械"]
    watermark_chars: set = frozenset("四川省住房和城乡建设厅信息公开浏览专用")
    price_row_labels: list = ["基价", "人工费", "材料费", "机械费"]
    name_section_header: str = "项目"
