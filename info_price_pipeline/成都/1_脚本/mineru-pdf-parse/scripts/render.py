"""
把 MinerU /file_parse 的 result.json 拆成 .md 和 .html 预览页。

用法:
    python render.py <result.json> [<源 PDF 路径>]

产物: 与 result.json 同目录下的 <stem>.md 和 <stem>.html

说明:
  - 输出文件名优先用源 PDF 的 basename（中文），避免 JSON 里 file_names
    字段因 multipart double-encode 出现乱码。
  - 不指定源 PDF 时，回退到 JSON 的 results 第一个 key（如果它是纯 ASCII）。
"""
import argparse
import datetime
import html
import json
import re
import sys
from pathlib import Path

# Windows GBK 编码修复：强制 stdout/stderr 用 utf-8（跟 step3/4/5/6 对齐）
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def parse_ts(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def split_md_to_segments(md: str):
    """把 md_content 切成 (kind, payload) 段，kind in {h1,h2,h3,p,t}。"""
    TABLE_RE = re.compile(r"<table.*?</table>", re.S)
    segments = []
    pos = 0
    for m in TABLE_RE.finditer(md):
        pre = md[pos:m.start()]
        for line in pre.split("\n"):
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("### "):   segments.append(("h3", line[4:]))
            elif line.startswith("## "): segments.append(("h2", line[3:]))
            elif line.startswith("# "):  segments.append(("h1", line[2:]))
            else:                        segments.append(("p", line))
        segments.append(("t", m.group(0)))
        pos = m.end()
    for line in md[pos:].split("\n"):
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("### "):   segments.append(("h3", line[4:]))
        elif line.startswith("## "): segments.append(("h2", line[3:]))
        elif line.startswith("# "):  segments.append(("h1", line[2:]))
        else:                        segments.append(("p", line))
    return segments


def render_html(stem: str, segments, data: dict, source_pdf: str | None,
                cl_count: int, mj_pages: int) -> str:
    rows = [f"<h1>MinerU 输出预览 — {html.escape(stem)}.pdf</h1>"]
    # task metadata（completed_at/created_at/task_id）在 /tasks/{id}/result 里没有，
    # 只有 /tasks/{id} (status) 才有。兼容两种 JSON 结构：缺失则显示 N/A。
    if "completed_at" in data and "created_at" in data:
        delta_s = (parse_ts(data["completed_at"]) - parse_ts(data["created_at"])).total_seconds()
        delta_str = f"{delta_s:.1f}s"
    else:
        delta_str = "N/A"
    task_id = data.get("task_id", "N/A")
    meta = (
        f"<b>backend:</b> {data.get('backend', 'N/A')} &nbsp;|&nbsp; "
        f"<b>version:</b> {data.get('version', 'N/A')} &nbsp;|&nbsp; "
        f"<b>用时:</b> {delta_str}<br>"
        f"<b>task_id:</b> {task_id}<br>"
        f"<b>源 PDF:</b> {html.escape(source_pdf) if source_pdf else '(未指定)'}<br>"
        f"<b>content_list items:</b> {cl_count} &nbsp;|&nbsp; "
        f"<b>middle_json pages:</b> {mj_pages} &nbsp;|&nbsp; "
        f"<b>tables:</b> {sum(1 for k, _ in segments if k == 't')}"
    )
    rows.append(f"<div class='meta'>{meta}</div>")

    table_idx = 0
    for kind, payload in segments:
        if kind in ("h1", "h2", "h3"):
            rows.append(f"<{kind}>{html.escape(payload)}</{kind}>")
        elif kind == "p":
            rows.append(f"<p>{html.escape(payload)}</p>")
        elif kind == "t":
            table_idx += 1
            rows.append(
                f"<div class='tbl-wrap'>"
                f"<div class='tbl-cap'>表 {table_idx}</div>"
                f"{payload}"
                f"</div>"
            )

    body = "\n".join(rows)
    css = """
      body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
             max-width: 1200px; margin: 24px auto; padding: 0 16px; color:#222; }
      h1 { border-bottom: 2px solid #444; padding-bottom: 6px; }
      h2 { color:#1a4d8c; margin-top: 28px; border-left: 4px solid #1a4d8c; padding-left: 8px; }
      h3 { color:#444; }
      p  { line-height: 1.7; }
      .meta { color:#666; font-size:12px; line-height:1.6;
              background:#f5f5f5; padding:8px 12px; border-radius:4px; margin-bottom:16px; }
      .tbl-wrap { margin: 12px 0 28px; border: 1px solid #ccc; padding: 0;
                  overflow-x: auto; background: #fafafa; }
      .tbl-cap { background: #1a4d8c; color:#fff; padding: 4px 10px; font-size: 13px; }
      table { border-collapse: collapse; width: 100%; font-size: 13px; background:#fff; }
      td { border: 1px solid #bbb; padding: 4px 8px; vertical-align: middle; text-align: center; }
      td:empty { background:#f0f0f0; }
      tr:first-child td { background:#eef3fa; font-weight:600; }
    """
    return f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<title>MinerU 输出预览 — {html.escape(stem)}</title>
<style>{css}</style>
</head><body>
{body}
<hr>
<p style='color:#888;font-size:11px'>源 result.json 路径见 SKILL.md 文档。</p>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description="把 MinerU result.json 转 .md + .html")
    ap.add_argument("result_json", help="MinerU 返回的 result.json 路径")
    ap.add_argument("source_pdf", nargs="?", default=None,
                    help="源 PDF 路径（推荐提供，输出文件名用它的 basename）")
    args = ap.parse_args()

    result_json = Path(args.result_json).resolve()
    out_dir = result_json.parent

    data = json.loads(result_json.read_text(encoding="utf-8"))
    if not data.get("results"):
        print(f"❌ {result_json} 里 results 为空", file=sys.stderr)
        sys.exit(1)

    # 决定输出文件名 stem
    if args.source_pdf:
        stem = Path(args.source_pdf).stem
    else:
        first_key = next(iter(data["results"]))
        try:
            first_key.encode("ascii")
            stem = first_key
            print(f"⚠ 未指定源 PDF，输出文件名用 JSON 的 key: {stem!r}", file=sys.stderr)
        except UnicodeEncodeError:
            print(f"❌ JSON file_names 含非 ASCII: {first_key!r}", file=sys.stderr)
            print(f"  请传入源 PDF 路径作为第二参数（推荐）", file=sys.stderr)
            sys.exit(1)

    content_key = next(iter(data["results"]))
    content = data["results"][content_key]
    md = content["md_content"]
    cl = json.loads(content["content_list"])
    mj = json.loads(content["middle_json"])

    # ---- 写 .md ----
    md_path = out_dir / f"{stem}.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"wrote {md_path}  ({len(md):,} chars)")

    # ---- 写 .html ----
    segments = split_md_to_segments(md)
    html_str = render_html(
        stem, segments, data, args.source_pdf,
        cl_count=len(cl), mj_pages=len(mj.get("pdf_info", [])),
    )
    html_path = out_dir / f"{stem}.html"
    html_path.write_text(html_str, encoding="utf-8")
    table_count = sum(1 for k, _ in segments if k == "t")
    print(f"wrote {html_path}  ({len(html_str):,} chars, {table_count} tables)")

    # ---- 摘要 ----
    if "completed_at" in data and "created_at" in data:
        delta_s = (parse_ts(data["completed_at"]) - parse_ts(data["created_at"])).total_seconds()
        duration_str = f"{delta_s:.1f}s"
    else:
        duration_str = "N/A"
    print()
    print(f"backend:  {data.get('backend', 'N/A')}")
    print(f"version:  {data.get('version', 'N/A')}")
    print(f"duration: {duration_str}")
    print(f"open:     file:///{html_path.as_posix()}")


if __name__ == "__main__":
    main()