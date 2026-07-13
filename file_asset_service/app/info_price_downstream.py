from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FileAsset, FileProcessing
from app.storage import ObjectStore


def load_extract_payload(session: Session, storage: ObjectStore, processing_id: str) -> tuple[FileProcessing, FileAsset, dict]:
    task = session.get(FileProcessing, processing_id)
    if task is None:
        raise ValueError(f"processing task not found: {processing_id}")
    asset = session.get(FileAsset, task.file_id)
    if asset is None:
        raise ValueError(f"file asset not found: {task.file_id}")
    if task.processor != "info_price_parse" or task.status != "succeeded":
        raise ValueError(f"processing task is not a succeeded info_price_parse task: {processing_id}")
    if not task.output_bucket or not task.output_key:
        raise ValueError(f"processing task has no output: {processing_id}")
    return task, asset, json.loads(storage.get_object(task.output_bucket, task.output_key))


def list_info_price_extracts(session: Session, storage: ObjectStore, limit: int = 100) -> list[dict[str, object]]:
    capped_limit = max(1, min(limit, 500))
    statement = (
        select(FileProcessing, FileAsset)
        .join(FileAsset, FileProcessing.file_id == FileAsset.file_id)
        .where(FileProcessing.processor == "info_price_parse")
        .where(FileProcessing.status == "succeeded")
        .where(FileProcessing.output_bucket.is_not(None))
        .where(FileProcessing.output_key.is_not(None))
        .order_by(FileProcessing.finished_at.desc(), FileProcessing.processing_id.desc())
        .limit(capped_limit)
    )

    extracts: list[dict[str, object]] = []
    for task, asset in session.execute(statement).all():
        payload = json.loads(storage.get_object(task.output_bucket, task.output_key))
        extracts.append(
            {
                "processing_id": task.processing_id,
                "file_id": task.file_id,
                "file_name": asset.file_name,
                "tenant_code": asset.tenant_code,
                "schema": payload.get("schema"),
                "row_count": payload.get("row_count", len(payload.get("rows", []))),
                "finished_at": task.finished_at.isoformat() if task.finished_at else None,
                "next_step": "review",
            }
        )
    return extracts


def build_review_view(task: FileProcessing, asset: FileAsset, payload: Mapping[str, object]) -> dict[str, object]:
    rows = _extract_rows(payload)
    pending_material_matches = _pending_material_matches(rows)
    low_confidence_items = _low_confidence_items(rows)
    manual_indexes = {item["row_index"] for item in pending_material_matches + low_confidence_items}
    manual_count = len(manual_indexes)
    total_rows = len(rows)
    return {
        "processing_id": task.processing_id,
        "file_id": asset.file_id,
        "file_name": asset.file_name,
        "tenant_code": asset.tenant_code,
        "role": "reviewer",
        "role_note": "复核是加工：把系统抽取结果改对，再提交审核。",
        "scope_note": "只处理待人工项，不逐行看",
        "total_rows": total_rows,
        "manual_count": manual_count,
        "auto_confirmed_count": max(total_rows - manual_count, 0),
        "pending_material_matches": pending_material_matches[:50],
        "low_confidence_items": low_confidence_items[:50],
        "quality_gate": {
            "secondary_action": "打回重解析",
            "primary_action": "确认并提交审核",
            "write_enabled": False,
            "note": "v0.1 只读观测；review/audit 表定稿后再写入状态。",
        },
    }


def build_audit_view(task: FileProcessing, asset: FileAsset, payload: Mapping[str, object]) -> dict[str, object]:
    rows = _extract_rows(payload)
    outliers = _audit_outliers(rows)
    missing_or_out_of_range = _missing_or_out_of_range(rows)
    sample_rows = [_sample_row(row, index) for index, row in enumerate(rows[:5], start=1)]
    return {
        "processing_id": task.processing_id,
        "file_id": asset.file_id,
        "file_name": asset.file_name,
        "tenant_code": asset.tenant_code,
        "role": "auditor",
        "role_note": "审核是质检：判定本期数据能不能卖，不能直接改数据。",
        "total_rows": len(rows),
        "can_edit_data": False,
        "editable_actions": [],
        "can_release": False,
        "metrics": {
            "price_rows": len(rows),
            "profile_status": "blocked_missing_baseline",
            "needs_review": len({item["row_index"] for item in outliers + missing_or_out_of_range}),
            "previous_period_match_rate": None,
        },
        "previous_period_compare": {
            "status": "missing_baseline",
            "message": "缺上期基准，不能完成正式审核",
        },
        "anomaly_profile": {
            "period_compare": {
                "status": "missing_baseline",
                "message": "与上期对比必须实现；v0.1 尚无上期价格基准。",
            },
            "peer_outliers": outliers[:50],
            "missing_or_out_of_range": missing_or_out_of_range[:50],
        },
        "sample_rows": sample_rows,
        "release_signature": {
            "warning": "放行即签名：本期价格表将进入发布池，可对外销售。v0.1 因缺上期基准禁止正式放行。",
            "required": True,
            "auditor": "当前审核人",
        },
        "return_to_review": {
            "action": "打回复核",
            "requires_row_and_reason": True,
            "note": "审核人发现问题只能打回复核，由复核人修改。",
        },
    }


def _extract_rows(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _pending_material_matches(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        raw_name = _text(row.get("原始名称") or row.get("名称"))
        if not raw_name:
            continue
        if any(token in raw_name for token in ("未匹配", "新材料", "未知", "其他")):
            confidence = 0
            suggestion = ""
        else:
            continue
        items.append(
            {
                "row_index": index,
                "raw_material_name": raw_name,
                "suggested_material_name": suggestion,
                "confidence": confidence,
                "source_location": _source_location(row, index),
                "actions": ["确认", "换", "登记新品种"],
            }
        )
    return items


def _low_confidence_items(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        reasons = _row_risk_reasons(row)
        for field, message in reasons:
            items.append(
                {
                    "row_index": index,
                    "field": field,
                    "raw_material_name": _text(row.get("原始名称") or row.get("名称")),
                    "suggested_value": _text(row.get(field)),
                    "reason": message,
                    "source_location": _source_location(row, index),
                    "actions": ["采纳", "改"],
                }
            )
    return items


def _audit_outliers(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        name = _text(row.get("原始名称") or row.get("名称"))
        price = _number(row.get("综合单价"))
        if price is None:
            continue
        if _looks_like_steel_material(name) and price < 1000:
            items.append(
                {
                    "row_index": index,
                    "raw_material_name": name,
                    "signal": "同类离群",
                    "message": "钢材价格低于同类常见数量级，疑似掉位或少一位。",
                    "source_location": _source_location(row, index),
                }
            )
    return items


def _missing_or_out_of_range(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        for field, message in _row_risk_reasons(row):
            items.append(
                {
                    "row_index": index,
                    "raw_material_name": _text(row.get("原始名称") or row.get("名称")),
                    "field": field,
                    "signal": "缺位/越界",
                    "message": message,
                    "source_location": _source_location(row, index),
                }
            )
    return items


def _row_risk_reasons(row: Mapping[str, object]) -> list[tuple[str, str]]:
    reasons: list[tuple[str, str]] = []
    unit = _text(row.get("单位"))
    price_text = _text(row.get("综合单价"))
    without_tax_text = _text(row.get("不含税价"))
    name = _text(row.get("原始名称") or row.get("名称"))
    price = _number(price_text)
    if not unit:
        reasons.append(("单位", "单位为空，无法确认计价口径。"))
    if not price_text:
        reasons.append(("综合单价", "综合单价为空。"))
    if price is not None and price <= 0:
        reasons.append(("综合单价", "综合单价小于等于 0。"))
    if price is not None and _looks_like_steel_material(name) and price < 1000:
        reasons.append(("综合单价", "钢材价格低于同类常见数量级，疑似抽取错位。"))
    if price_text and without_tax_text and _number(without_tax_text) is not None and price is not None:
        without_tax = _number(without_tax_text)
        if without_tax and price < without_tax:
            reasons.append(("综合单价", "含税价低于不含税价，口径疑似反向。"))
    return reasons


def _sample_row(row: Mapping[str, object], index: int) -> dict[str, object]:
    return {
        "row_index": index,
        "raw_material_name": _text(row.get("原始名称") or row.get("名称")),
        "unit": _text(row.get("单位")),
        "price": _text(row.get("综合单价")),
        "source_location": _source_location(row, index),
        "actions": ["一致", "存疑"],
    }


def _looks_like_steel_material(name: str) -> bool:
    if any(token in name for token in ("塑钢", "不锈钢")):
        return False
    return any(token in name for token in ("螺纹钢", "盘条", "圆钢", "型钢", "钢筋"))


def _source_location(row: Mapping[str, object], index: int) -> str:
    source = _text(row.get("来源")) or "Sheet1"
    return f"{source} 第 {index + 1} 行"


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _number(value: object) -> float | None:
    try:
        text = _text(value)
        if not text:
            return None
        return float(text.replace(",", ""))
    except ValueError:
        return None
