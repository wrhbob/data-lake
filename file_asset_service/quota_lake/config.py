"""配置 dataclass — 所有可调参数集中定义, 不从环境变量读取 (此时不涉及运行时配置)."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LakeConfig:
    """定额数据湖管线配置。"""
    # ── OCR ──
    ocr_lang: str = "ch"
    ocr_use_gpu: bool = False
    render_dpi: int = 300

    # ── 资源对齐 ──
    resource_align_auto_threshold: float = 0.92  # n-gram 相似度 ≥ 阈值 自动对齐

    # ── QA ──
    qa_price_tolerance: float = 0.05  # 基价校验容差 (元)
    qa_pass_rate_target: float = 0.99  # P0 目标通过率

    # ── 输出 ──
    output_jsonl: bool = True
    output_db: bool = True

    # ── 调试 ──
    keep_page_images: bool = False
    verbose: bool = False


_DEFAULT = LakeConfig()


def get_lake_config() -> LakeConfig:
    return _DEFAULT
