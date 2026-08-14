#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
profile_schema.py — Feature flag schema（SPEC §3.5 / §4）

Python 与前端共用 single source of truth（Pydantic）。

设计决策（v0.4）：
- profile 只存 **P 类 flag（P1-P7）**：这些是「输出结构差异」，必须按省设值（SPEC §4.2）。
- **B 类（B1-B7）不进 profile**：它们是 baseline 行为（多关键字 OR + 可选检测），
  内嵌在 baseline 代码里；若未来需覆盖关键字集合，再在 schema 加 override 字段。
- **§3.4 示例的 `composite_label` 字段未纳入**：它是 B1 的 profile 侧覆盖，
  B1 作为 baseline 多关键字 OR 已内嵌（综合基价/综合单价/全费用/基价 OR），职责不重复。
- **version 字段**：profile 是持久化数据、关联档案，schema 演进必须有版本号（SPEC §7-3）。
- **预置 5 个 profile**：sc-2018 / cq-2018 / gd-2018 / hu-2018 / bj-2021（SPEC §3.2）。

字段名与 SPEC §4.2 对齐（v0.4：P7 由 B8+原 P7 合并为 `cross_page_strategy`）。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


# ─────────────────────────────────────────────────────────────────────
# 枚举值（P1-P7 的取值；SPEC §5 详细说明 + §9.2 验收清单）
# ─────────────────────────────────────────────────────────────────────


class SectionSystem(str, Enum):
    """P3 章节体系（SPEC §5 P3）。"""

    ALPHANUMERIC = "alphanumeric"  # sc/cq/gd: ## A.1.5 混凝土工程
    CHINESE4 = "chinese4"  # hu: ## 第一章 / 一、 / 1. / (1)
    MIXED_ZH = "mixed-zh"  # bj: ## 第X章 → ## 第X节 → ## X、
    ZH_CE = "zh-ce"  # sh: ## 第一册 → ## 第一章 → ## 1．节（数字节直接挂在章下）


class MaterialHeaderLayout(str, Enum):
    """P2 材料表头列结构（SPEC §5 P2）。"""

    NAME_UNIT_PRICE_QTY = "name-unit-price-qty"  # sc/hu: 名称|单位|单价|消耗量
    CODE_NAME_UNIT_PRICE_QTY = "code-name-unit-price-qty"  # cq: (空)|编码|名称|单位|单价|消耗量
    CLASS_CODE_NAME_UNIT_PRICE_QTY = "class-code-name-unit-price-qty"  # gd: 分类|编码|名称|单位|单价|消耗量
    CLASS_NAME_UNIT_PRICE_QTY = "class-name-unit-price-qty"  # yn: 分类|名称|单位|单价|消耗量（无编码列，名称可跨2列）
    CLASS_CODE_NAME_UNIT_QTY = "class-code-name-unit-qty"  # bj: 分类|编码|名称|单位|消耗量（无单价列）
    CLASS_NAME_UNIT_QTY_RIGHT_PRICE = "class-name-unit-qty-right-price"
    # 深圳 2023 单子目形态：分类|名称(跨2)|单位|数量|价格(最右)。
    #   价格列 = "2023年8月工料机参考价格(元)"，位于表格最右侧，数量在左。


class FeeEmitStrategy(str, Enum):
    """P5 费/类行 emit 决策（SPEC §5 P5）。"""

    ALWAYS = "always"  # sc：永远 emit
    THREE_CORRESPONDENCE = "three-correspondence"  # cq/gd/hu：下方有对应分类则不 emit
    NONE = "none"  # bj：无价类信息，不 emit 费行（extract_table_bj 自闭环）


class MaterialFeeAutoEmit(str, Enum):
    """P6 料-材料费自动行 emit（SPEC §5 P6）。"""

    NONE = "none"  # bj：extract_table_bj 无此机制
    SC = "sc-style"  # has_unpriced OR (无已计价料行 AND 材料费>0)
    CQ = "cq-style"  # 材料费行存在 AND 无料/配分类 AND 值>0（无全局 PID 检查）
    GD = "gd-style"  # cq + pid_material_global 检查
    HU = "hu-style"  # = gd（含 pid_material_global 检查）


class CrossPageStrategy(str, Enum):
    """P7 跨页续表策略（SPEC §5 P7；v0.4 由 B8 + 原 P7 合并）。

    v0.4 修正：实测 gd/hu 的跨页续表走同一套 join 逻辑（extract_table 检测
    continuation → process_md_file 把续表料/机行插入主表对应 PID 段）。原设计的
    skip 二分从未被实现（gd 实际也是 join），收敛为 none/join 两态。
    """

    NONE = "none"  # sc/cq/bj：无跨页续表
    JOIN = "join"  # gd/hu：跨页拼接续表（续表料/机行插入主表对应 PID 段）


# ─────────────────────────────────────────────────────────────────────
# Profile（SPEC §3.4）
# ─────────────────────────────────────────────────────────────────────


class FeatureFlagProfile(BaseModel):
    """profile = 一组 feature flag 的命名快照。

    只含 P 类 flag；B 类（B1-B7）是 baseline 行为，内嵌在 baseline 代码。
    """

    model_config = {"extra": "forbid"}  # 未知字段直接报错——profile 是严格快照

    name: str = Field(
        ...,
        pattern=r"^[a-z]{2}-\d{4}$",
        description="格式 {省}-{版本年}，如 sc-2018",
    )
    version: int = Field(
        default=1,
        ge=1,
        description="schema 版本（SPEC §7-3：演进必须有版本号，否则历史档案引用失效）",
    )

    # P1 项目编码 regex
    project_id_regex: str = Field(
        ...,
        description="定额项目编号 regex（SPEC §5 P1）",
    )

    # P2 材料表头布局
    material_header_layout: MaterialHeaderLayout

    # P3 章节体系
    section_system: SectionSystem

    # P4 段行 emit 层级
    section_depth: int = Field(..., ge=1, le=4)

    # P5 费/类行 emit 决策
    fee_emit_strategy: FeeEmitStrategy

    # P6 料-材料费自动行 emit
    material_fee_auto_emit: MaterialFeeAutoEmit

    # P7 跨页续表策略
    cross_page_strategy: CrossPageStrategy = CrossPageStrategy.NONE

    # ── P 类行为开关（v0.4 新增：从 FeatureContext 移入 profile）──
    #   这些开关按省设值（输出结构/内容差异，SPEC §4.2 P 类），显式声明、
    #   不依赖 features.py 检测。machine_labels 是 B 类关键字 override：
    #   SPEC §3.4 预留"B 类 override 字段"，gd 的"机具费"因省差异在此覆盖
    #   （见 SPEC §4.4 并集误伤反例）。
    material_sort: bool = Field(
        default=False,
        description="材料行 emit 排序（gd/hu 附项语义 料→附项→机；其余按 PDF 行序）",
    )
    filter_project_numeric: bool = Field(
        default=False,
        description="项目名纯数字过滤（hu/cq '项目'block 数字污染；sc 规格档位保留）",
    )
    unit_last_line_override: bool = Field(
        default=False,
        description="'计量单位=见表'时用 block 最后一行覆盖单位列（gd/bj）",
    )
    filter_toc_dotted: bool = Field(
        default=False,
        description="段行名含点线页码/mermaid 语法则跳过（gd TOC 噪声；sc 省略号保留）",
    )
    strict_section_re2: bool = Field(
        default=False,
        description="段行 Pass4 用严格正则拒 mermaid（gd；sc 用宽松版）",
    )
    machine_labels: tuple[str, ...] = Field(
        default=("机械费", "施工机具使用费"),
        description="机械费行 label 集合（B 类 override；gd 追加 '机具费'）",
    )
    strict_section_downstream: bool = Field(
        default=False,
        description="节段行验证遇同级节提前终止（yn 工程量计算规则条目'一、/十三、'下方混入"
                    "同级条目+定额工作内容需拒绝；hu 说明条目当段行 emit 需宽松保持原版行为）",
    )
    bracket_qty_is_unpriced: bool = Field(
        default=False,
        description="数量带括号='未计价主材'（yn 规则：材料栏带'（）'的数量为未计价材，价值未计"
                    "入基价）。5 省括号数量='比例行'（配，sc/hu 实测），语义相反 → profile 区分",
    )
    skip_price_col_in_qty: bool = Field(
        default=False,
        description="数量提取跳过单价列（yn：4-1-599 表 8 PID 定额编号 colspan=4，单价列落在 "
                    "PID[0] 列，不跳过会把单价当数量 → 验证 565.63×565.63 荒谬值）。"
                    "5 省默认 False：sc HC0001 表 OCR 把'单位单价(元)'合并，数量列数与 PID 错位，"
                    "sc 原版把单价当数量（错误的保真行为），跳过会破坏 0 diff 回归 → 必须按省设值",
    )
    labor_name_keywords: tuple[str, ...] = Field(
        default=(),
        description="名称含这些关键字 → category='工' 且不计验证（河南：'综合工日'是人工，无 "
                    "col0 标签，单价'-'数量带括号，算不出人工费 → 人工费以费行为准，综合工日"
                    "只保留消耗量参考）。云南 col0='人工'标签已由 B3 处理，不冲突。默认空",
    )
    force_emit_labor_fee: bool = Field(
        default=False,
        description="人工费行强制 emit（河南：综合工日无单价，人工费行值与明细匹配不上 → 必须写"
                    "人工费行，覆盖 three-correspondence 的'下方有人工明细则隐藏'。材料/机械费行"
                    "仍正常 three-correspondence——它们的明细有单价可匹配）。默认 False",
    )
    machine_unit_keywords: tuple[str, ...] = Field(
        default=(),
        description="单位含这些关键字 → category='机'（河南：材料明细区无 col0 分类标签，区分"
                    "材料/机械靠单位='台班'。默认空 → 不影响有标签的省）",
    )
    extra_cost_labels: dict[str, str] = Field(
        default_factory=dict,
        description="费行 label → 综行名（河南特有费：{'其他措施费':'其他措施费','安文费':'安文费',"
                    "'规费':'规费'}，各自独立成综行。baseline 默认只识别 管理费/利润/费用/增值税/"
                    "一般风险费 五种综行，新费类必须走本字段）。默认空",
    )
    material_header_implicit: bool = Field(
        default=False,
        description="材料区无「名称」表头行（上海：表直接从 定额编号/项目/单位 块跳到 人工/机械/"
                    "材料 分类行）。baseline 默认靠 col0='名称'/'工料机名称' 定位材料区，上海无表头"
                    "→ 需从 项目 块后第一个 B3 分类标签行开始。默认 False 不影响 7 省",
    )
    project_unit_last_line: bool = Field(
        default=False,
        description="项目单位 = 项目/子目名称 block 最后一行（上海：表无「计量单位」行，单位是"
                    "项目名称块最后一行如 $m^3$）。baseline 默认只在该行='见表' 时用最后一行覆盖；"
                    "上海无计量单位行且无 '见表' → 需强制用最后一行。默认 False 不影响 7 省",
    )
    dedup_section_ids: bool = Field(
        default=False,
        description="段行 sec_id 全局去重（上海 zh-ce：OCR 每页重复 `## 1． 一般土方` 节头 → 段行"
                    "重复 4-6 遍）。**仅当该省的段行 code 全局唯一才可开**——sc/hu 的段行 code 可"
                    "跨卷合法重复（如 sc 多卷共用 A.1.1），开去重会误删。zh-ce（册.章.节）天然唯一。"
                    "默认 False 不影响 7 省",
    )

    @model_validator(mode="after")
    def _check_section_depth_system(self) -> "FeatureFlagProfile":
        """P3 ↔ P4 联动（SPEC §4.3）：章节体系决定可 emit 的最大深度。

        alphanumeric → 1，chinese4 → 4，mixed-zh → 3。
        非法组合（如 alphanumeric + depth=4）在保存 profile 时就拒绝。
        """
        expected = {
            SectionSystem.ALPHANUMERIC: 1,
            SectionSystem.CHINESE4: 4,
            SectionSystem.MIXED_ZH: 3,
            SectionSystem.ZH_CE: 3,
        }
        want = expected[self.section_system]
        if self.section_depth != want:
            raise ValueError(
                f"section_depth={self.section_depth} 与 section_system="
                f"{self.section_system.value} 不匹配（应为 {want}，SPEC §4.3）"
            )
        return self

    @model_validator(mode="after")
    def _check_none_consistency(self) -> "FeatureFlagProfile":
        """无价类（P5=none）时 P6 必须为 none（bj 场景，SPEC §5 P5/P6）。

        有价类的省 P5 才有意义；P5=none（无价类信息）→ 不可能有料-材料费自动行。
        """
        if (
            self.fee_emit_strategy == FeeEmitStrategy.NONE
            and self.material_fee_auto_emit != MaterialFeeAutoEmit.NONE
        ):
            raise ValueError(
                "fee_emit_strategy=none（无价类信息）时 "
                "material_fee_auto_emit 必须也是 none"
            )
        return self


# ─────────────────────────────────────────────────────────────────────
# 预置 5 个 profile（SPEC §3.2）
# ─────────────────────────────────────────────────────────────────────


PRESET_PROFILES: dict[str, FeatureFlagProfile] = {
    "sc-2018": FeatureFlagProfile(
        name="sc-2018",
        project_id_regex=r"^[A-Z]{1,2}\d{4}$",  # MB0082 / AA0001
        material_header_layout=MaterialHeaderLayout.NAME_UNIT_PRICE_QTY,
        section_system=SectionSystem.ALPHANUMERIC,
        section_depth=1,
        fee_emit_strategy=FeeEmitStrategy.ALWAYS,
        material_fee_auto_emit=MaterialFeeAutoEmit.SC,
        cross_page_strategy=CrossPageStrategy.NONE,
    ),
    "cq-2018": FeatureFlagProfile(
        name="cq-2018",
        project_id_regex=r"^[A-Z]{1,2}\d{4}$",  # MB0082 / AA0001
        material_header_layout=MaterialHeaderLayout.CODE_NAME_UNIT_PRICE_QTY,
        section_system=SectionSystem.ALPHANUMERIC,
        section_depth=1,
        fee_emit_strategy=FeeEmitStrategy.THREE_CORRESPONDENCE,
        material_fee_auto_emit=MaterialFeeAutoEmit.CQ,
        cross_page_strategy=CrossPageStrategy.NONE,
    ),
    "gd-2018": FeatureFlagProfile(
        name="gd-2018",
        project_id_regex=r"^[A-Z]\d+-\d+-\d+$",  # C1-5-13
        material_header_layout=MaterialHeaderLayout.CLASS_CODE_NAME_UNIT_PRICE_QTY,
        section_system=SectionSystem.ALPHANUMERIC,
        section_depth=1,
        fee_emit_strategy=FeeEmitStrategy.THREE_CORRESPONDENCE,
        material_fee_auto_emit=MaterialFeeAutoEmit.GD,
        cross_page_strategy=CrossPageStrategy.JOIN,  # 实测 gd 跨页也是 join
        material_sort=True,
        unit_last_line_override=True,
        filter_toc_dotted=True,
        strict_section_re2=True,
        machine_labels=("机械费", "施工机具使用费", "机具费"),
    ),
    "hu-2018": FeatureFlagProfile(
        name="hu-2018",
        project_id_regex=r"^[A-Z]\d+-\d+$",  # G1-1
        material_header_layout=MaterialHeaderLayout.NAME_UNIT_PRICE_QTY,
        section_system=SectionSystem.CHINESE4,
        section_depth=4,
        fee_emit_strategy=FeeEmitStrategy.THREE_CORRESPONDENCE,
        material_fee_auto_emit=MaterialFeeAutoEmit.HU,
        cross_page_strategy=CrossPageStrategy.JOIN,
        material_sort=True,
        filter_project_numeric=True,
    ),
    "hn-2018": FeatureFlagProfile(
        name="hn-2018",
        project_id_regex=r"^[A-Z]\d+-\d+$",  # B1-1（湖南省仿古建筑工程消耗量标准基价表）
        material_header_layout=MaterialHeaderLayout.NAME_UNIT_PRICE_QTY,
        section_system=SectionSystem.CHINESE4,
        section_depth=4,
        fee_emit_strategy=FeeEmitStrategy.THREE_CORRESPONDENCE,
        material_fee_auto_emit=MaterialFeeAutoEmit.HU,
        cross_page_strategy=CrossPageStrategy.JOIN,
        material_sort=True,
        filter_project_numeric=True,
        strict_section_downstream=True,  # 规则条目（工程量计算规则）不当段行；章节下方必须低级章节或定额
    ),
    "sz-2023": FeatureFlagProfile(
        name="sz-2023",
        project_id_regex=r"^\d{6}-\d+$",  # 010001-1（6 位章节码-子目序号，深圳市建筑工程消耗量标准）
        material_header_layout=MaterialHeaderLayout.CLASS_NAME_UNIT_QTY_RIGHT_PRICE,
        section_system=SectionSystem.CHINESE4,  # TODO(sz): 需新增 NUMERIC 枚举（## 1 / ## 1.3 / ## 1.3.1 数字多级）
        section_depth=4,
        fee_emit_strategy=FeeEmitStrategy.THREE_CORRESPONDENCE,
        material_fee_auto_emit=MaterialFeeAutoEmit.NONE,  # 深圳表含 材料费/机械费 费行（全费用构成区），不自动 emit
        cross_page_strategy=CrossPageStrategy.NONE,
        filter_project_numeric=True,
    ),
    "bj-2021": FeatureFlagProfile(
        name="bj-2021",
        project_id_regex=r"^\d{1,2}-\d{1,3}$",  # 1-1 / 2-21（纯数字 章-定额号）
        material_header_layout=MaterialHeaderLayout.CLASS_CODE_NAME_UNIT_QTY,
        section_system=SectionSystem.MIXED_ZH,
        section_depth=3,
        fee_emit_strategy=FeeEmitStrategy.NONE,  # 无价类信息
        material_fee_auto_emit=MaterialFeeAutoEmit.NONE,
        cross_page_strategy=CrossPageStrategy.NONE,
        unit_last_line_override=True,
    ),
    "yn-2020": FeatureFlagProfile(
        name="yn-2020",
        project_id_regex=r"^\d+-\d+-\d+$",  # 4-1-1（数字-数字-数字，云南省园林绿化工程计价标准 DBJ53/T-60-2020）
        material_header_layout=MaterialHeaderLayout.CLASS_NAME_UNIT_PRICE_QTY,
        section_system=SectionSystem.CHINESE4,
        section_depth=4,
        fee_emit_strategy=FeeEmitStrategy.THREE_CORRESPONDENCE,
        material_fee_auto_emit=MaterialFeeAutoEmit.CQ,
        cross_page_strategy=CrossPageStrategy.NONE,
        strict_section_downstream=True,  # 规则条目（工程量计算规则/总说明条款）不当段行
        bracket_qty_is_unpriced=True,  # 括号数量=未计价苗木（非比例行）
        skip_price_col_in_qty=True,  # 数量跳过单价列（8 PID colspan=4 表防单价当数量）
    ),
    "sh-2016": FeatureFlagProfile(
        name="sh-2016",
        project_id_regex=r"^\d{2}-\d+-\d+-\d+$",  # 04-1-1-1 / 04-2-5-52（专业-章-节-定额号）
        material_header_layout=MaterialHeaderLayout.CLASS_CODE_NAME_UNIT_QTY,
        section_system=SectionSystem.ZH_CE,  # 第一册 → 第一章 → 1．节
        section_depth=3,
        fee_emit_strategy=FeeEmitStrategy.NONE,  # 无基价/费用（同北京）
        material_fee_auto_emit=MaterialFeeAutoEmit.NONE,
        cross_page_strategy=CrossPageStrategy.NONE,
        unit_last_line_override=True,  # 项目单位在 项目名称 block 最后一行
        material_header_implicit=True,  # 材料区无「名称」表头，从项目块后首个 人工/材料/机械 行开始
        project_unit_last_line=True,  # 无「计量单位」行，项目单位 = 项目块最后一行（$m^3$）
        dedup_section_ids=True,  # OCR 每页重复节头 → 段行去重（zh-ce code 全局唯一，安全）
    ),
    "ha-2016": FeatureFlagProfile(
        name="ha-2016",
        project_id_regex=r"^\d+-\d+$",  # 1-5 / 2-6（数字-数字，河南省房屋建筑与装饰工程预算定额 HA 01-31-2016）
        material_header_layout=MaterialHeaderLayout.NAME_UNIT_PRICE_QTY,
        section_system=SectionSystem.CHINESE4,
        section_depth=4,
        fee_emit_strategy=FeeEmitStrategy.THREE_CORRESPONDENCE,
        material_fee_auto_emit=MaterialFeeAutoEmit.HU,
        cross_page_strategy=CrossPageStrategy.NONE,
        machine_labels=("机械费", "施工机具使用费", "机械使用费"),  # 河南费行用"机械使用费"
        strict_section_downstream=True,  # 规则条目（工程量计算规则/总说明）不当段行
        labor_name_keywords=("综合工日",),  # 综合工日是人工（无 col0 标签，单价'-'数量带括号）
        force_emit_labor_fee=True,  # 人工费行强制 emit（综合工日无单价，人工费匹配不上明细）
        machine_unit_keywords=("台班",),  # 无分类标签，单位=台班 → 机械
        bracket_qty_is_unpriced=True,  # 括号数量=未计价主材（预制桩等单价'-'数量(10.10)，价值未计入基价）
        extra_cost_labels={"其他措施费": "其他措施费", "安文费": "安文费", "规费": "规费"},  # 河南特有费 → 各自综行
    ),
}


def get_preset_profile(name: str) -> FeatureFlagProfile:
    """取预置 profile；新省份复制最相似 profile → 改名 → 微调（SPEC §3.2）。"""
    try:
        return PRESET_PROFILES[name]
    except KeyError:
        raise KeyError(
            f"未知 profile '{name}'。预置：{sorted(PRESET_PROFILES)}。"
            f"新省份请复制最相似预置 profile 后微调，不允许从零自创（SPEC §3.2）。"
        ) from None
