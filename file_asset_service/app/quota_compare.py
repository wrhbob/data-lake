"""
P0-4B · 跨省定额对比端点（基于 quota-compare 数据层）。

POST /api/data-lake/quota/compare
  Form params: keyword (必填), any_terms (空格分隔), exclude_terms (空格分隔)
  返回: xlsx 字节流（Content-Disposition: attachment）

设计要点（来自 2026-08-17 plan）：
  1. 同步路由 → threading.Lock 串行防御（consistent with info_price_parse._JOBS_LOCK）
  2. 4 省硬编码（51/44/41/50），不暴露省份选择
  3. 解析完成的 archive 指 final_xlsx_key，parse_status ∈ {qa_passed, usable}
  4. 单个 archive 拉取/解析失败 → 跳过该档案、不整体报错
  5. 4 省都没数据 → 422 NO_QUOTA_ARCHIVES
  6. 不写临时文件，直接 Response(bytes) 返回（方案 1A）

import 路径：quota_compare.py 启动时把 repo_root/quota-compare 加入 sys.path，
  确保 `import extract` 命中兄弟目录的脚本（顶层 sys.path 注入比 -m 显式 import 更稳定）。
"""
from __future__ import annotations

import logging
import sys
import threading
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db_session
from app.models import AdministrativeDivision, Archive
from app.quota_parser import PARSE_BUCKET_CANDIDATE
from app.storage import get_object_store

logger = logging.getLogger(__name__)

# ── 0. 把 quota-compare/ 加入 sys.path ──────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]
_QUOTA_COMPARE_DIR = _REPO_ROOT / "quota-compare"
if _QUOTA_COMPARE_DIR.is_dir() and str(_QUOTA_COMPARE_DIR) not in sys.path:
    sys.path.insert(0, str(_QUOTA_COMPARE_DIR))

import extract as _extract  # noqa: E402  # 必须在 sys.path 注入之后

# ── 1. 省份名解析（不限 4 省：查 administrative_division 表全集）───
# region_code 是 6 位 GB/T 2260，前 2 位 = 省级代码。
# 末 4 位 0000 命中省级行（440000 / 510000 / 500000 / 110000 等）。
# 缓存在模块层（34 行数据，每请求查一次即可，进程内不需 refresh）。
_PROVINCE_NAME_CACHE: dict[str, str] = {}


def _resolve_province_name(session: Session, region_code: str) -> str | None:
    """把 6 位 region_code 翻译成 GB/T 2260 省级中文名（不限 4 省）"""
    rc = (region_code or "").strip()
    if len(rc) < 2:
        return None
    prefix2 = rc[:2]
    if prefix2 in _PROVINCE_NAME_CACHE:
        return _PROVINCE_NAME_CACHE[prefix2]
    name = session.scalar(
        select(AdministrativeDivision.name)
        .where(AdministrativeDivision.code == prefix2 + "0000")
        .where(AdministrativeDivision.level == "province")
        .where(AdministrativeDivision.enabled.is_(True))
    )
    if name:
        _PROVINCE_NAME_CACHE[prefix2] = name
    return name

# ── 2. 同步串行锁（与 info_price_parse._JOBS_LOCK 一致）───────────
_COMPARE_LOCK = threading.Lock()

# ── 3. Router ──────────────────────────────────────────────────────
router = APIRouter(prefix="/api/data-lake/quota", tags=["quota"])


# ── 4. 内部 helpers ────────────────────────────────────────────────

def _archive_to_province(archive: Archive, session: Session) -> str | None:
    """从 region_code 前 2 位映射到省级中文名（不限 4 省，查 administrative_division）。"""
    return _resolve_province_name(session, archive.region_code)


def _split_terms(s: str | None) -> list[str]:
    return [t for t in (s or "").split() if t]


def _sanitize_filename(s: str) -> str:
    """Windows 文件名禁用字符替换。"""
    bad = '<>:"/\\|?*'
    return "".join("_" if ch in bad else ch for ch in s).strip() or "未命名"


# ── 5. 端点 ────────────────────────────────────────────────────────

@router.post("/compare")
def compare_quota_across_provinces(
    keyword: str = Form(..., description="定额名称必须包含的关键词"),
    any_terms: str = Form("", description="扩展命中词（空格分隔，OR）"),
    exclude_terms: str = Form("", description="排除词（空格分隔）"),
    session: Session = Depends(get_db_session),
) -> Response:
    """跨省定额对比：拉取所有已审核省份的 final.xlsx，按 keyword/any/exclude 命中并合表。

    设计：方案 1A —— 直接返回 xlsx 字节流，不写临时文件。
    """
    keyword = (keyword or "").strip()
    if not keyword:
        raise HTTPException(422, detail="EMPTY_KEYWORD")

    any_t = _split_terms(any_terms)
    exclude_t = _split_terms(exclude_terms)

    # 5.1 取所有可用档案（已审核 + final_xlsx 准备好）
    done_status = ("qa_passed", "usable")
    archives = list(session.execute(
        select(Archive).where(
            Archive.domain_type == "quota",
            Archive.is_withdrawn.is_(False),
            Archive.parse_status.in_(done_status),
            Archive.final_xlsx_key.isnot(None),
        )
    ).scalars().all())

    # 5.2 按省份分组
    by_province: dict[str, list[tuple[str, bytes]]] = {}
    skipped_region = set()
    for arc in archives:
        prov = _archive_to_province(arc, session)
        if prov is None:
            skipped_region.add((arc.archive_id, arc.region_code))
            continue
        try:
            data = get_object_store().get_object(PARSE_BUCKET_CANDIDATE, arc.final_xlsx_key)
        except (KeyError, Exception) as exc:
            logger.warning("compare: skip archive %s 拉取失败: %s", arc.archive_id, exc)
            continue
        by_province.setdefault(prov, []).append((arc.archive_id, data))
    if skipped_region:
        logger.info("compare: skip %d archives (region_code 无省级映射): %s",
                    len(skipped_region), skipped_region)

    if not by_province:
        raise HTTPException(
            422,
            detail={
                "code": "NO_QUOTA_ARCHIVES",
                "message": "所有省份都没有已审核的 final.xlsx，请先完成入库与审核",
            },
        )

    # 5.3 串行收集命中块（threading.Lock 防御并发大文件内存压力）
    with _COMPARE_LOCK:
        try:
            blocks_by_prov, summary, _ = _extract.collect_hits(
                by_province, keyword=keyword, any_terms=any_t, exclude_terms=exclude_t,
            )
        except Exception as exc:
            logger.exception("compare: collect_hits 失败")
            raise HTTPException(500, detail=f"COLLECT_HITS_FAILED: {exc}") from exc

    total = sum(summary.values())
    if total == 0:
        # 所有省份都有档案，但没有一个匹配 keyword
        raise HTTPException(
            422,
            detail={
                "code": "NO_HITS",
                "message": f"keyword='{keyword}' 在已审核省份未命中任何定额",
                "summary": dict(summary),
                "by_province": {p: len(by_province[p]) for p in sorted(by_province)},
            },
        )

    # 5.4 写 xlsx 字节
    sheet_title = f"{keyword}对比"
    try:
        out_bytes = _extract.write_xlsx_bytes(blocks_by_prov, sheet_title=sheet_title)
    except Exception as exc:
        logger.exception("compare: write_xlsx_bytes 失败")
        raise HTTPException(500, detail=f"WRITE_XLSX_FAILED: {exc}") from exc

    # 5.5 响应：双重 Content-Disposition（ASCII + RFC 5987 UTF-8）
    filename_ascii = f"{keyword}_跨省对比.xlsx".encode("ascii", "replace").decode("ascii")
    filename_utf8 = quote(f"{keyword}_跨省对比.xlsx")
    return Response(
        content=out_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{filename_ascii}\"; "
                f"filename*=UTF-8''{filename_utf8}"
            ),
            "X-Compare-Total": str(total),
            "X-Compare-Summary": quote(str(dict(summary))),
        },
    )
