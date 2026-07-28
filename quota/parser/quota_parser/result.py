"""quota_parser 结果数据类（v0.2）"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StageAResult:
    """阶段 A：PDF → candidate xlsx（含 autofinalize 5 步 + 自动 QA）"""
    task_id: str
    status: str  # "candidate_ready" | "qa_warning" | "failed"
    parser_version: str
    profile: str
    source_sha256: str
    source_pdf_path: str
    ocr_markdown_path: str | None
    ocr_result_json_path: str | None
    candidate_xlsx_path: str
    candidate_xlsx_sha256: str
    issues_md_path: str | None
    qa_report_json_path: str | None
    qa_report_md_path: str | None
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, int | str] = field(default_factory=dict)


@dataclass
class StageBResult:
    """阶段 B：reviewed xlsx → final xlsx（不再跑 finalize）"""
    task_id: str
    status: str  # "final_ready" | "qa_warning" | "failed"
    parser_version: str
    final_xlsx_path: str
    final_xlsx_sha256: str
    qa_report_json_path: str | None
    qa_report_md_path: str | None
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, int | str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(self)


def stage_a_to_dict(result: StageAResult) -> dict[str, Any]:
    import dataclasses
    return dataclasses.asdict(result)