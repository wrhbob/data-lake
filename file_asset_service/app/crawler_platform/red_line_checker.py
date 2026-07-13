from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import FileProcessing, IngestEvent


@dataclass
class RedLineCheck:
    rows_created: int = 0
    passes: bool = True
    violations: list[dict] = field(default_factory=list)


def check_file_processing_red_lines(task_ids: list[str], db: Session) -> RedLineCheck:
    if not task_ids:
        return RedLineCheck()

    rows = (
        db.query(FileProcessing, IngestEvent.task_id)
        .join(IngestEvent, IngestEvent.file_id == FileProcessing.file_id)
        .filter(IngestEvent.task_id.in_(task_ids))
        .all()
    )
    violations = [
        {
            "processing_id": processing.processing_id,
            "file_id": processing.file_id,
            "task_id": task_id,
            "processor": processing.processor,
            "status": processing.status,
            "rule": "FileProcessing count must be 0 for cost_info Layer 0",
        }
        for processing, task_id in rows
    ]
    return RedLineCheck(rows_created=len(violations), passes=not violations, violations=violations)
