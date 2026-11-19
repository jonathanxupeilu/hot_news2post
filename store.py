"""运行记录与文章草稿的本地存储。"""
import json
import time
from pathlib import Path


class RunStore:
    def __init__(self, base: Path):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)

    # ---- meta ----
    def _meta_path(self, run_id: str) -> Path:
        return self.base / run_id / "meta.json"

    def _write_meta(self, run_id: str, meta: dict) -> None:
        p = self._meta_path(run_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_meta(self, run_id: str) -> dict | None:
        p = self._meta_path(run_id)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def list_runs(self) -> list[dict]:
        runs = []
        for d in self.base.iterdir():
            if not d.is_dir():
                continue
            meta = self.get_meta(d.name)
            if meta:
                runs.append(meta)
        runs.sort(key=lambda m: m.get("run_id", ""), reverse=True)
        return runs

    def set_status(self, run_id: str, status: str, error: str | None = None) -> None:
        meta = self.get_meta(run_id) or {}
        meta["status"] = status
        if error:
            meta["error"] = error
        self._write_meta(run_id, meta)

    # ---- events ----
    def append_event(self, run_id: str, event: dict) -> None:
        p = self.base / run_id / "events.jsonl"
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_events(self, run_id: str) -> list[dict]:
        p = self.base / run_id / "events.jsonl"
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    # ---- runs ----
    def create_run(self) -> str:
        run_id = time.strftime("%Y%m%d-%H%M%S")
        d = self.base / run_id
        n = 1
        while d.exists():
            n += 1
            run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{n}"
            d = self.base / run_id
        d.mkdir(parents=True)
        (d / "events.jsonl").touch()
        self._write_meta(run_id, {
            "run_id": run_id,
            "status": "created",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "articles": [],
        })
        return run_id

    # ---- articles ----
    def save_articles(self, run_id: str, parsed: dict) -> None:
        meta = self.get_meta(run_id) or {}
        raw_path = self.base / run_id / "raw.txt"
        raw_path.write_text(parsed.get("raw") or "", encoding="utf-8")
        articles_meta = []
        if parsed.get("ok"):
            arts_dir = self.base / run_id / "articles"
            arts_dir.mkdir(exist_ok=True)
            for art in parsed["articles"]:
                idx = art["idx"]
                fname = f"{idx:02d}.md"
                (arts_dir / fname).write_text(art["draft"], encoding="utf-8")
                articles_meta.append({
                    "idx": idx,
                    "topic": art["topic"],
                    "file": f"articles/{fname}",
                    "platform": art.get("platform", "legacy"),
                    "platform_name": art.get("platform_name", "初稿"),
                })
            meta["parsed_ok"] = True
            meta["parse_error"] = None
            meta["platforms"] = parsed.get("platforms", [])
        else:
            meta["parsed_ok"] = False
            meta["parse_error"] = parsed.get("error")
            meta["platforms"] = []
        meta["articles"] = articles_meta
        meta["skipped"] = parsed.get("skipped", 0)  # 过滤掉的拒答数，留痕供归因
        meta["normalized"] = parsed.get("normalized", 0)  # 结构重排数，留痕供归因
        self._write_meta(run_id, meta)

    def read_article(self, run_id: str, idx: int) -> str:
        p = self.base / run_id / "articles" / f"{idx:02d}.md"
        if not p.exists():
            raise FileNotFoundError(f"文章不存在: {p}")
        return p.read_text(encoding="utf-8")

    def write_article(self, run_id: str, idx: int, content: str) -> None:
        p = self.base / run_id / "articles" / f"{idx:02d}.md"
        p.write_text(content, encoding="utf-8")
