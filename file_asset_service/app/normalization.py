from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_QUERY_KEYS = {"spm", "from", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}
TRACKING_QUERY_PREFIXES = ("utm_",)
TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    {
        "價": "价",
        "資": "资",
        "訊": "讯",
        "綜": "综",
        "設": "设",
        "烏": "乌",
        "魯": "鲁",
        "齊": "齐",
        "築": "筑",
        "與": "与",
        "標": "标",
        "準": "准",
    }
)


def normalize_key_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").translate(TRADITIONAL_TO_SIMPLIFIED).strip().lower()
    text = re.sub(r"[《》“”\"'`]+", "", text)
    text = re.sub(r"[（）()【】\[\]{}]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_source_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    query_pairs = []
    for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in TRACKING_QUERY_KEYS or any(key_lower.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        query_pairs.append((key, item_value))
    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, netloc, parsed.path, query, ""))
