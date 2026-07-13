# -*- coding: utf-8 -*-
"""
定额子目"语义坐标"构建 + 清单自动套定额
========================================
思路: 每个定额子目 = 一个语义坐标点, 由三层构成
  ① 硬坐标(离散, 用于过滤):   清单编码前缀(章节印刷的"编码:010101")、计量单位量纲、章节路径
  ② 软坐标(连续, 用于召回):   embedding( 章节路径 + 子目名称 + 工作内容 )
  ③ 特征坐标(结构化, 用于精排): 从名称中抽取的参数槽位 —— 土壤类别/深度/厚度/强度等级/材质/施工方式

清单项(GB50500 12位编码 + 项目名称 + 项目特征描述) 套定额流程:
  1. 编码前6/9位 -> 章节硬过滤(候选缩到几十条)
  2. 清单"名称+特征"embedding 与候选定额软坐标做余弦相似 -> Top-K
  3. 特征槽位规则比对(深度2m以内 vs 特征"挖深1.5m" 命中) -> 精排
  4. 输出 定额编号 + 置信度 + 命中依据, 低置信度标记人工复核

依赖: pip install sentence-transformers  (推荐中文模型 BAAI/bge-large-zh-v1.5)
      离线也可退化为 jieba+BM25(内置降级实现)
"""
import json
import re
import sqlite3
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 特征槽位抽取: 定额名称/清单特征 -> 结构化参数
# ---------------------------------------------------------------------------

SLOT_PATTERNS = {
    "soil_class":  re.compile(r"([一二三四]、?[一二三四]?类[土石]|[一二三四]类[土石]|坚[石土]|软石|普坚石)"),
    "depth":       re.compile(r"(?:深|挖深|深度|挖土深度)\s*[::为]?\s*(\d+(?:\.\d+)?)\s*m\s*(以内|以外|以上|以下)?"),
    "thickness":   re.compile(r"(?:厚|厚度)\s*[::为]?\s*(\d+(?:\.\d+)?)\s*(mm|cm|m)\s*(以内|以外)?"),
    "strength":    re.compile(r"(C\d{2,3}|M\d+(?:\.\d+)?|MU\d+)"),
    "diameter":    re.compile(r"(?:直径|Φ|φ|D)\s*(\d+(?:\.\d+)?)\s*(mm)?\s*(以内|以外)?"),
    "height":      re.compile(r"(?:高|高度|檐高)\s*(\d+(?:\.\d+)?)\s*m\s*(以内|以外)?"),
    "method":      re.compile(r"(人工|机械|泵送|非泵送|现浇|预制|预拌|干拌|湿拌)"),
}

def extract_slots(text: str) -> dict:
    slots = {}
    for k, pat in SLOT_PATTERNS.items():
        m = pat.search(text or "")
        if m:
            slots[k] = m.group(0)
    return slots

def slot_score(list_slots: dict, quota_slots: dict) -> float:
    """槽位比对得分: 命中+1, 冲突-1, 定额有而清单未提及 0"""
    if not quota_slots:
        return 0.0
    s = 0
    for k, qv in quota_slots.items():
        lv = list_slots.get(k)
        if lv is None:
            continue
        if k in ("depth", "thickness", "diameter", "height"):
            if _range_match(lv, qv):
                # 最紧界优先: 1.8m 套"2m以内"应优于"4m以内"
                lvn, qvn = _num(lv), _num(qv)
                tight = 1.0 / (1.0 + abs(qvn - lvn)) if (lvn and qvn) else 0
                s += 1 + 0.5 * tight
            else:
                s -= 1
        else:
            s += 1 if (lv in qv or qv in lv) else -1
    return s / max(len(quota_slots), 1)

def _num(s):
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None

def _range_match(list_val: str, quota_val: str) -> bool:
    """清单给具体值(挖深1.5m), 定额给区间(深度2m以内) -> 判断落入"""
    lv, qv = _num(list_val), _num(quota_val)
    if lv is None or qv is None:
        return False
    if "以内" in quota_val or "以下" in quota_val:
        return lv <= qv
    if "以外" in quota_val or "以上" in quota_val:
        return lv > qv
    return abs(lv - qv) < 1e-6

# ---------------------------------------------------------------------------
# 语义坐标文本模板
# ---------------------------------------------------------------------------

def quota_semantic_text(q: dict) -> str:
    """定额子目 -> 参与embedding的规范化文本(软坐标)"""
    return " | ".join(x for x in [
        "/".join(q.get("chapter_path", [])),
        q.get("name", ""),
        f"单位:{q.get('unit','')}",
        f"工作内容:{q.get('work_content','')}",
    ] if x and not x.endswith(":"))

def listing_semantic_text(code: str, name: str, features: str, work: str = "") -> str:
    """清单项 -> 查询文本"""
    return " | ".join(x for x in [name, features, work] if x)

# ---------------------------------------------------------------------------
# 向量后端: sentence-transformers 优先, 离线降级 BM25
# ---------------------------------------------------------------------------

class Embedder:
    def __init__(self, model_name="BAAI/bge-large-zh-v1.5"):
        self.mode = "st"
        try:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(model_name)
        except Exception:
            self.mode = "bm25"
            self.model = None

    def encode(self, texts):
        if self.mode == "st":
            return self.model.encode(texts, normalize_embeddings=True)
        return None  # bm25 走关键词路径

class BM25Fallback:
    """无网络/无模型时的降级召回: 字符2-gram + BM25"""
    def __init__(self, docs):
        import math
        self.docs = [self._grams(d) for d in docs]
        self.df = {}
        for d in self.docs:
            for g in set(d):
                self.df[g] = self.df.get(g, 0) + 1
        self.N = len(docs); self.avg = sum(len(d) for d in self.docs) / max(self.N, 1)
        self.math = math

    @staticmethod
    def _grams(t):
        t = re.sub(r"\s+", "", t)
        return [t[i:i+2] for i in range(len(t) - 1)]

    def score(self, query):
        q = set(self._grams(query)); out = []
        for i, d in enumerate(self.docs):
            s, L = 0.0, len(d)
            tf = {}
            for g in d:
                tf[g] = tf.get(g, 0) + 1
            for g in q:
                if g in tf:
                    idf = self.math.log(1 + (self.N - self.df[g] + 0.5) / (self.df[g] + 0.5))
                    s += idf * tf[g] * 2.2 / (tf[g] + 1.2 * (0.25 + 0.75 * L / self.avg))
            out.append(s)
        return out

# ---------------------------------------------------------------------------
# 索引与匹配
# ---------------------------------------------------------------------------

class QuotaIndex:
    def __init__(self, dinge_json: str):
        with open(dinge_json, encoding="utf-8") as f:
            self.items = json.load(f)["items"]
        for q in self.items:
            q["_sem"] = quota_semantic_text(q)
            q["_slots"] = extract_slots(q["name"] + " " + q.get("work_content", ""))
        self.emb = Embedder()
        if self.emb.mode == "st":
            import numpy as np
            self.vecs = self.emb.encode([q["_sem"] for q in self.items])
            self.np = np
        else:
            self.bm25 = BM25Fallback([q["_sem"] for q in self.items])

    def match(self, list_code: str, name: str, features: str = "", topk: int = 5):
        """清单项 -> 候选定额(带置信度)"""
        # ① 硬过滤: 清单编码前6位 == 章节印刷编码
        prefix6 = re.sub(r"\D", "", list_code)[:6]
        cand = [i for i, q in enumerate(self.items)
                if q.get("list_code_prefix", "").startswith(prefix6[:6])] if prefix6 else []
        if not cand:                     # 编码没建上索引则全量召回
            cand = list(range(len(self.items)))

        # ② 软坐标召回
        query = listing_semantic_text(list_code, name, features)
        if self.emb.mode == "st":
            qv = self.emb.encode([query])[0]
            sims = [(i, float(self.np.dot(self.vecs[i], qv))) for i in cand]
        else:
            all_s = self.bm25.score(query)
            mx = max((all_s[i] for i in cand), default=1) or 1
            sims = [(i, all_s[i] / mx) for i in cand]
        sims.sort(key=lambda x: -x[1])
        sims = sims[:max(topk * 4, 20)]

        # ③ 槽位精排
        ls = extract_slots(name + " " + features)
        ranked = []
        for i, s in sims:
            q = self.items[i]
            sc = 0.7 * s + 0.3 * (0.5 + 0.5 * slot_score(ls, q["_slots"]))
            ranked.append((sc, s, q))
        ranked.sort(key=lambda x: -x[0])
        return [{
            "quota_code": q["code"], "quota_name": q["name"], "unit": q["unit"],
            "base_price": q["base_price"], "confidence": round(sc, 3),
            "semantic_sim": round(s, 3),
            "slot_hits": {k: v for k, v in q["_slots"].items() if k in ls},
            "chapter": "/".join(q.get("chapter_path", [])),
            "need_review": sc < 0.55,
        } for sc, s, q in ranked[:topk]]

# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    idx = QuotaIndex(sys.argv[1] if len(sys.argv) > 1 else "./output/dinge.json")
    # 示例: 一条清单项
    demo = idx.match(
        list_code="010101002001",
        name="挖一般土方",
        features="1.土壤类别:三类土 2.挖土深度:1.8m 3.弃土运距:自行考虑",
    )
    print(json.dumps(demo, ensure_ascii=False, indent=1))
