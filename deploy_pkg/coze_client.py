"""coze.cn 工作流 SSE 流式客户端（httpx 直连 OpenAPI）。"""
import json
from typing import Iterator

import httpx

API_BASE = "https://api.coze.cn"


class CozeError(Exception):
    pass


def run_workflow_stream(token: str, workflow_id: str,
                        parameters: dict | None = None,
                        timeout_s: int = 1800) -> Iterator[dict]:
    """执行工作流并逐个 yield 事件：
    {"type": "message", "content", "node_title", "node_seq_id"}
    {"type": "done", "output"}
    {"type": "error", "message"}
    {"type": "other", "event", "content"}
    """
    payload = {"workflow_id": workflow_id, "stream": True,
               "parameters": parameters or {}}
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    timeout = httpx.Timeout(timeout_s, connect=10.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", f"{API_BASE}/v1/workflow/run",
                               headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = resp.read().decode("utf-8", "replace")
                    raise CozeError(f"Coze API HTTP {resp.status_code}: {body[:500]}")
                ctype = resp.headers.get("content-type", "")
                if "text/event-stream" in ctype:
                    yield from _iter_sse_events(resp)
                else:
                    # coze.cn /v1/workflow/run 实测返回 application/json（同步模式）
                    body = resp.read().decode("utf-8", "replace")
                    yield from _json_response_events(body)
    except httpx.HTTPError as e:
        raise CozeError(f"Coze 请求失败: {e}") from e


def _json_response_events(body: str) -> Iterator[dict]:
    yield {"type": "other", "event": "raw", "content": body[:1000]}
    try:
        obj = json.loads(body)
    except Exception:
        raise CozeError(f"响应既非 SSE 也非 JSON: {body[:300]}")
    if isinstance(obj, dict) and obj.get("code") not in (0, None):
        raise CozeError(f"Coze 错误 {obj.get('code')}: {obj.get('msg')}")
    if isinstance(obj, dict):
        output = obj.get("data") or obj.get("output") or obj.get("content")
        if isinstance(output, (dict, list)):
            output = json.dumps(output, ensure_ascii=False)
        if not (isinstance(output, str) and output):
            output = json.dumps(obj, ensure_ascii=False)
        yield {"type": "done", "output": output}
    else:
        yield {"type": "done", "output": json.dumps(obj, ensure_ascii=False)}


def _iter_sse_events(resp: httpx.Response) -> Iterator[dict]:
    event = None
    data_lines: list[str] = []
    for line in resp.iter_lines():
        if line is None:
            continue
        line = line.rstrip("\r")
        if not line:  # 空行 = 事件结束
            if event is not None or data_lines:
                yield _make_event(event, "\n".join(data_lines))
                event, data_lines = None, []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if event is not None or data_lines:
        yield _make_event(event, "\n".join(data_lines))


def _make_event(event: str | None, data: str) -> dict:
    name = (event or "").strip()
    if name.lower() == "done":
        return {"type": "done", "output": _done_output(data)}
    if name.lower() in ("error", "fail"):
        return {"type": "error", "message": _error_message(data)}
    try:
        obj = json.loads(data)
    except Exception:
        obj = None
    if isinstance(obj, dict):
        return {"type": "message",
                "content": obj.get("content", data),
                "node_title": obj.get("node_title", ""),
                "node_seq_id": str(obj.get("node_seq_id", ""))}
    return {"type": "other", "event": name or "unknown", "content": data}


def _done_output(data: str) -> str | None:
    s = (data or "").strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
    except Exception:
        return s
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for k in ("output", "content", "data", "result"):
            v = obj.get(k)
            if isinstance(v, str) and v:
                return v
            if isinstance(v, (list, dict)):
                return json.dumps(v, ensure_ascii=False)
        return json.dumps(obj, ensure_ascii=False)
    return json.dumps(obj, ensure_ascii=False)


def _error_message(data: str) -> str:
    try:
        obj = json.loads(data)
        if isinstance(obj, dict):
            return str(obj.get("msg") or obj.get("message") or data)
    except Exception:
        pass
    return data or "工作流执行失败"
