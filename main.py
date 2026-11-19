"""FastAPI 入口：触发 Coze 工作流、轮询事件、读写草稿。"""
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from coze_client import CozeError, run_workflow_stream
from parser import parse_output
from store import RunStore

load_dotenv()

BASE = Path(__file__).resolve().parent
# 本地默认 ./data/runs；创空间容器内由 app.py 设 DATA_DIR=/mnt/workspace/hot_news2post/runs（持久化）
store = RunStore(Path(os.getenv("DATA_DIR") or BASE / "data" / "runs"))

app = FastAPI(title="hot_news2post")

_lock = threading.Lock()
_runs: dict[str, dict] = {}  # run_id -> {"events": [...], "status": str, "started": float}


class RunParams(BaseModel):
    parameters: dict = {}


class ArticleBody(BaseModel):
    content: str


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/config")
def config():
    token = os.getenv("COZE_API_TOKEN", "").strip()
    wf_id = os.getenv("COZE_WORKFLOW_ID", "").strip()
    return {"configured": bool(token and "xxx" not in token and wf_id)}


@app.post("/api/runs")
def create_run(params: RunParams = RunParams()):
    token = os.getenv("COZE_API_TOKEN", "").strip()
    wf_id = os.getenv("COZE_WORKFLOW_ID", "").strip()
    if not token or "xxx" in token or not wf_id:
        raise HTTPException(400, "请先在 .env 配置 COZE_API_TOKEN / COZE_WORKFLOW_ID（真实值）")
    run_id = store.create_run()
    with _lock:
        _runs[run_id] = {"events": [], "status": "running", "started": time.time()}
    store.set_status(run_id, "running")
    threading.Thread(target=_execute, args=(run_id, token, wf_id, params.parameters),
                     daemon=True).start()
    return {"run_id": run_id}


def _execute(run_id: str, token: str, wf_id: str, parameters: dict) -> None:
    def push(ev: dict) -> None:
        with _lock:
            _runs[run_id]["events"].append(ev)
        store.append_event(run_id, ev)

    try:
        final_raw = None
        messages: list[dict] = []
        for ev in run_workflow_stream(token, wf_id, parameters):
            push(ev)
            if ev["type"] == "message":
                messages.append(ev)
            elif ev["type"] == "done":
                final_raw = ev.get("output")
            elif ev["type"] == "error":
                raise CozeError(ev.get("message") or "工作流执行失败")
        if not final_raw and messages:
            def seq(m):
                try:
                    return float(m.get("node_seq_id") or 0)
                except ValueError:
                    return 0.0
            messages.sort(key=seq)
            final_raw = messages[-1].get("content") or "".join(
                m.get("content") or "" for m in messages)
        if not final_raw:
            raise CozeError("未从事件流中提取到工作流输出")
        parsed = parse_output(final_raw)
        store.save_articles(run_id, parsed)
        with _lock:
            _runs[run_id]["status"] = "done"
        store.set_status(run_id, "done")
    except Exception as e:
        with _lock:
            _runs[run_id]["status"] = "error"
        store.set_status(run_id, "error", error=str(e))


@app.get("/api/runs")
def list_runs():
    return store.list_runs()


@app.get("/api/runs/{run_id}")
def run_meta(run_id: str):
    meta = store.get_meta(run_id)
    if meta is None:
        raise HTTPException(404, f"运行不存在: {run_id}")
    with _lock:
        st = _runs.get(run_id)
    if st:
        meta["status"] = st["status"]
        meta["elapsed"] = round(time.time() - st["started"], 1)
    return meta


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str, offset: int = 0):
    with _lock:
        st = _runs.get(run_id)
        events = list(st["events"]) if st else []
    if st:
        status = st["status"]
    else:
        status = (store.get_meta(run_id) or {}).get("status", "unknown")
    return {"status": status, "events": events[offset:]}


@app.get("/api/runs/{run_id}/articles")
def list_articles(run_id: str):
    meta = store.get_meta(run_id)
    if meta is None:
        raise HTTPException(404, f"运行不存在: {run_id}")
    articles = meta.get("articles", [])
    platforms = meta.get("platforms")
    if not platforms:  # 兼容旧 run：从 articles 推导平台清单
        order, grouped = [], {}
        for a in articles:
            key = a.get("platform", "legacy")
            if key not in grouped:
                order.append(key)
                grouped[key] = {"key": key, "name": a.get("platform_name", "初稿"),
                                "count": 0, "skipped": 0}
            grouped[key]["count"] += 1
        platforms = [grouped[k] for k in order]
    return {"platforms": platforms, "articles": articles}


@app.get("/api/runs/{run_id}/articles/{idx}")
def get_article(run_id: str, idx: int):
    try:
        return {"content": store.read_article(run_id, idx)}
    except FileNotFoundError:
        raise HTTPException(404, f"文章不存在: idx={idx}")


@app.put("/api/runs/{run_id}/articles/{idx}")
def put_article(run_id: str, idx: int, body: ArticleBody):
    try:
        store.write_article(run_id, idx, body.content)
    except FileNotFoundError:
        raise HTTPException(404, f"文章不存在: idx={idx}")
    return {"ok": True}


@app.get("/api/runs/{run_id}/raw")
def get_raw(run_id: str):
    p = store.base / run_id / "raw.txt"
    if not p.exists():
        raise HTTPException(404, "raw.txt 不存在（运行可能未完成）")
    return {"content": p.read_text(encoding="utf-8")}
