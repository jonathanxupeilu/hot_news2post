"""创空间部署入口：容器内以 0.0.0.0:7860 启动 FastAPI（扁平模块结构）。"""
import os
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    # 启动诊断：把容器内工作目录清单打进日志，出问题时自解释
    print("APP_ROOT:", ROOT, flush=True)
    print("ROOT entries:", sorted(p.name for p in ROOT.iterdir()), flush=True)
    # ModelScope Docker 创空间的持久化目录是 /mnt/workspace，其余路径重启即丢
    if not os.getenv("DATA_DIR"):
        os.environ["DATA_DIR"] = (
            "/mnt/workspace/hot_news2post/runs"
            if Path("/mnt/workspace").exists()
            else str(ROOT / "data" / "runs")
        )
    uvicorn.run("main:app", host="0.0.0.0", port=7860, workers=1)
