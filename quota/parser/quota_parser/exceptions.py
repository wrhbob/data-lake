"""quota_parser 异常体系（v0.2）

Worker 状态映射:
    transient(可自动重试)        : OcrUnavailableError / OcrTransientError
    failed_permanent(重试无效)    : ProfileExecutionError / InvalidPageRangeError / WorkdirNotWritableError
    failed_user(需人工介入)      : UnsupportedProvinceError / InvalidXlsxStructureError
"""
from __future__ import annotations


class QuotaParserError(Exception):
    """所有 quota_parser 异常的基类。"""


class OcrUnavailableError(QuotaParserError):
    """OCR 服务不可达 / 不健康 / 失败。Worker 应自动重试。"""


class OcrTransientError(QuotaParserError):
    """OCR 单段失败（>100 页分段时）。Worker 按段重试。"""


class UnsupportedProvinceError(QuotaParserError):
    """省份 code 不在注册表。Worker 标 failed_user。"""


class InvalidPageRangeError(QuotaParserError):
    """PDF 页数异常（0 页 / 无法读取）。Worker 标 failed_permanent。"""


class InvalidXlsxStructureError(QuotaParserError):
    """reviewed.xlsx 结构不合法（缺 Sheet1 / 列数错）。Worker 标 failed_user。"""


class ProfileExecutionError(QuotaParserError):
    """Profile 抽取失败。Worker 标 failed_permanent。"""


class WorkdirNotWritableError(QuotaParserError):
    """workspace 创建 / 写入失败。Worker 标 failed_permanent。"""