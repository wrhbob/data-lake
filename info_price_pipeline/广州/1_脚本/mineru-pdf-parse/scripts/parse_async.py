"""
MinerU 异步解析（用 /tasks 端点，避免长连接导致 uvicorn OOM）

用法:
    python parse_async.py <pdf_path>

为什么用异步端点:
  - /file_parse 同步：HTTP 连接保持到处理完成（436 页 ≈ 10 分钟），uvicorn 可能 OOM
  - /tasks 异步：HTTP 立即返回 task_id，客户端断开，服务端继续跑，结果存 24h
"""
import json
import sys
import time
from pathlib import Path

import requests

DEFAULT_API = "http://171.212.159.15:8000"


def submit_task(pdf: Path, api_url: str, backend="hybrid-engine", effort="high") -> str:
    """提交任务，返回 task_id。"""
    url = f"{api_url.rstrip('/')}/tasks"
    data = {
        "backend": backend,
        "effort": effort,
        "parse_method": "auto",
        "return_md": "true",
        "return_middle_json": "true",
        "return_content_list": "true",
        "formula_enable": "true",
        "table_enable": "true",
        "image_analysis": "true",
        "start_page_id": "0",
        "end_page_id": "99999",
    }
    print(f"📤 提交任务到 {url}")
    print(f"   PDF: {pdf.name} ({pdf.stat().st_size:,} bytes)")
    print(f"   backend={backend}, effort={effort}")

    t0 = time.time()
    with open(pdf, "rb") as f:
        files = {"files": (pdf.name, f, "application/pdf")}
        r = requests.post(url, files=files, data=data, timeout=300)
    r.raise_for_status()
    info = r.json()
    task_id = info["task_id"]
    print(f"✅ 任务已提交: {task_id}  ({time.time()-t0:.1f}s)")
    return task_id


def poll_status(task_id: str, api_url: str, interval: int = 15):
    """轮询直到任务完成/失败。"""
    url = f"{api_url}/tasks/{task_id}"
    print(f"\n⏳ 轮询状态 (间隔 {interval}s)... 按 Ctrl+C 中断")
    t0 = time.time()
    last_status = None
    while True:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            info = r.json()
        except Exception as e:
            print(f"   [{time.strftime('%H:%M:%S')}] poll error: {e}")
            time.sleep(interval)
            continue

        status = info.get("status")
        if status != last_status:
            print(f"   [{time.strftime('%H:%M:%S')}] status: {status}  ({time.time()-t0:.0f}s elapsed)")
            last_status = status

        if status in ("completed", "failed"):
            return info
        time.sleep(interval)


def fetch_result(task_id: str, api_url: str, out_path: Path) -> Path:
    """取最终结果，写到本地。"""
    url = f"{api_url}/tasks/{task_id}/result"
    print(f"\n📥 取结果: {url}")
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    out_path.write_bytes(r.content)
    print(f"💾 saved: {out_path}  ({len(r.content):,} bytes)")
    return out_path


def main():
    if len(sys.argv) != 2:
        print("usage: python parse_async.py <pdf_path>")
        sys.exit(1)

    pdf = Path(sys.argv[1]).resolve()
    if not pdf.exists():
        print(f"❌ PDF 不存在: {pdf}")
        sys.exit(1)

    out_dir = pdf.parent / pdf.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    result_json = out_dir / "result.json"

    # 1. 提交
    task_id = submit_task(pdf, DEFAULT_API)

    # 2. 轮询
    info = poll_status(task_id, DEFAULT_API)
    status = info.get("status")
    if status != "completed":
        print(f"\n❌ 任务失败: status={status}")
        print(f"   error: {info.get('error')}")
        sys.exit(2)

    # 3. 取结果
    print(f"\n✅ 任务完成（用时 {info.get('completed_at', '?')} - {info.get('created_at', '?')}）")
    fetch_result(task_id, DEFAULT_API, result_json)

    # 4. 渲染 .md + .html
    print(f"\n🎨 渲染 .md + .html ...")
    render_script = Path(r"C:\Users\wrhbob\.claude\skills\mineru-pdf-parse\scripts\render.py")
    if render_script.exists():
        import subprocess
        env = __import__("os").environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run(
            [sys.executable, str(render_script), str(result_json), str(pdf)],
            env=env, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            print(f"⚠ render.py 退出码 {r.returncode}")
    else:
        print(f"⚠ 找不到 {render_script}")

    print(f"\n📂 产物:")
    for f in sorted(out_dir.iterdir()):
        print(f"   {f.name}  ({f.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()