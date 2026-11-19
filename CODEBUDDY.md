# CODEBUDDY.md

This file provides guidance to CodeBuddy Code when working with code in this repository.

## 项目是什么

财经自媒体文章工作台：一个本地 FastAPI 单页 Web 应用。点击触发 Coze 工作流（抓新闻 → 选财经热点 → 知识库匹配分析框架 → 写初稿），实时展示事件流，查看/编辑初稿并导出 Markdown。单人日更场景，MVP 范围外明确不做：自动发布、定时任务、多账号、多工作流切换。

## 常用命令

依赖在 `requirements.txt`，虚拟环境在 `.venv`（Windows 下解释器为 `.venv/Scripts/python.exe`）。以下命令均在项目根目录执行。

```bash
# 安装依赖
pip install -r requirements.txt

# 本地启动（开发）：uvicorn 直接加载 main:app
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# Windows 若 uvicorn 不在 PATH：
.venv/Scripts/python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 云端/创空间启动入口：app.py 设定 DATA_DIR 后以 0.0.0.0:7860 运行
.venv/Scripts/python.exe app.py

# 全部测试
pytest
# 或
.venv/Scripts/python.exe -m pytest

# 单个测试（注意测试文件 import 的是根模块 parser，必须在项目根目录运行）
pytest tests/test_parser.py::test_array_of_strings
```

无 `pytest.ini` / `pyproject.toml` / `conftest.py`，测试全靠默认发现规则；`tests/` 无 `__init__.py`。

## 架构要点（读多个文件才能看清，先看这里）

### 1. 扁平模块布局 —— 最重要约束

所有 Python 模块都在**项目根目录**，不是 `app/` 包：`main.py`、`app.py`、`coze_client.py`、`parser.py`、`store.py`。**不要重新引入子目录包结构。**

原因：ModelScope 创空间的网页上传器会把文件夹结构拍平（`app/` 里的文件会被散到根目录，import 全乱）。`design.md` 第 6 节画的 `app/` 子目录是早期设计稿，已过时，以当前扁平布局为准。Dockerfile 也是逐个 `COPY main.py coze_client.py ... ./`。

### 2. 两个启动入口，一份 app

- `main.py`：定义 FastAPI `app` 与全部路由，是真正的应用主体。
- `app.py`：**仅**用于容器/创空间部署——设定 `DATA_DIR`（有 `/mnt/workspace` 则为 `/mnt/workspace/hot_news2post/runs`，否则回退本地 `data/runs`），然后 `uvicorn.run("main:app", host="0.0.0.0", port=7860, workers=1)`。启动时打印目录清单做自诊断。
- 本地开发走 `uvicorn main:app`，**不要**用 `app.py`（它会绑定 7860 并强改 DATA_DIR）。

### 3. 一次运行的数据流（core path）

```
POST /api/runs
  → main.create_run：校验 token/wf_id → store.create_run() 生成 run_id（时间戳）
  → 起一个 daemon 线程执行 main._execute，立即返回 {"run_id": ...}
      _execute：
        遍历 coze_client.run_workflow_stream(token, wf_id, parameters) 产出的事件
          - message/done/error/other 都经 push() 同时写入内存 _runs[run_id]["events"]
            和磁盘 store.append_event()（events.jsonl 原样透传，不加工不隐藏）
          - "done" 事件取 output 为 final_raw；若无则把所有 message 按 node_seq_id
            排序后取最后一条（或多条拼接）
          - parse_output(final_raw) → store.save_articles() 落盘
          - 更新状态 done / error（异常写入 meta.error）
前端轮询 GET /api/runs/{id}/events?offset=N 增量取事件、GET /api/runs/{id} 取状态与耗时。
```

- `_runs`（内存 dict）+ `_lock` 只服务**当前进程**的实时事件；磁盘才是持久化真源。进程重启后事件只能从 `events.jsonl` 读（但 `/events` 接口优先读内存，内存 miss 时回退 `store.get_meta` 的状态，注意此时不会返回历史事件）。
- 超时上限 600s，定义在 `coze_client.run_workflow_stream` 的 `timeout_s`。

### 4. Coze 客户端 `coze_client.py`

`run_workflow_stream()` 是 generator，yield 统一事件字典：`{"type": "message"|"done"|"error"|"other", ...}`。

- 调 `https://api.coze.cn/v1/workflow/run`，Bearer token 来自 `.env`。
- 同时兼容两种响应：`text/event-stream`（SSE，走 `_iter_sse_events`）与 `application/json`。**实测 coze.cn 该接口返回 JSON 同步模式**，走 `_json_response_events`，从 `data`/`output`/`content` 里抠 output。
- `CozeError` 是本模块自定义异常，`_execute` 会捕获并转成 error 状态。

### 5. 输出解析 `parser.py`（第一道质量门禁）

`parse_output(raw)` → `{"ok", "platforms":[{key,name,count,skipped}], "articles":[{idx,platform,platform_name,topic,draft}], "raw", "error", "skipped", "normalized"}`（`skipped` = 拒答数，写入 `meta.skipped`；`articles.idx` 为跨平台全局序号，store 据此落盘 `articles/NN.md`）。

- **协议（当前主路径）：工作流返回「按平台分组的对象」**——`{"X_output":[...], "gzh_output":[...], "lkdn_output":[...], "wb_output":[...], "xhs_output":[...]}`，每个数组是某平台风格适配的一篇或多篇初稿（整段 markdown 字符串）。这是口径，不要改回单篇对象。平台 key → 显示名见 `PLATFORM_NAMES`，未知 `*_output` key 也照常解析。
- **组织维度：平台为主**（2026-11-18 确认）。前端按平台 Tab 分类展示，不做跨平台话题对齐——各平台标题高度异质（X 英文反引号 / 公众号《》/ 微博话题标签 / 小红书 emoji），对齐不可靠。每篇用自己的标题，优先级：标题推荐段《》→「标题/主标题：」标注 → 全文《》→ 反引号英文标题 → 首行；无标题回退「话题N」。
- **存储/API**：store 按全局 idx 落盘，meta 存 `platforms` 清单 + 每篇 platform 字段；API `/articles` 返回 `{platforms, articles}`，旧 run 自动从 articles 推导平台清单。
- **拒答过滤**：命中「未涉及金融/无法创作/不符合财经…」且无《标题》的元素**过滤不入库**，按平台与总计记入 skipped。
- 结构规范化（兜底）：`_normalize_structure` 仅在两个段标题都在、标题在前、中间有 `---` 时交换两段，统一为「初稿在前 → 标题在后」；写入 `meta.normalized`，`raw.txt` 不动。workflow 二次修复后实测已统一（`normalized=0`），兜底保留作安全网。根因在 Coze 侧，根治要在 workflow 固定输出模板。
- 兼容旧形态：裸数组 / `{"output":[...]}` / `{"data":[...]}` 等旧单数组协议归入默认平台「初稿」；markdown 代码围栏、字符串/对象元素均兼容。
- 解析失败不丢内容：`ok=False`，原文仍写入 `raw.txt`，meta 标记 `parse_error`。新增解析规则时必须在 `tests/test_parser.py` 补 Golden 样例（用 Coze 真实输出形态）。
- **超时**：新 workflow 输出量约为旧版 5 倍，同步 JSON 返回可能超 600s，`coze_client.py` 默认 `timeout_s=1800`。

### 6. 存储 `store.py`（`RunStore`，纯文件系统）

根目录由 `DATA_DIR` 决定，本地默认 `./data/runs`。每次运行一个目录：

```
data/runs/<run_id>/
├── meta.json        # run_id / status / created_at / articles[] / parsed_ok / parse_error / error
├── events.jsonl     # 逐行 JSON，工作流事件流
├── raw.txt          # 原始输出（供复现/排错，解析失败也保留）
└── articles/
    ├── 01.md        # 按 1 起编号，文件名即 idx
    └── 02.md
```

`create_run()` 用 `%Y%m%d-%H%M%S` 命名，同秒冲突追加 `-2` 等后缀。编辑保存直接覆盖原 `.md`，无版本树（单人使用）。

### 7. API 路由（`main.py`）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/` | 返回 `static/index.html` |
| GET | `/api/config` | `{configured}`：token 与 wf_id 是否都真实配置（token 含 `xxx` 视为未配置） |
| POST | `/api/runs` | 触发运行，body `{"parameters": {...}}`，返回 `{run_id}` |
| GET | `/api/runs` | 运行列表（按 run_id 倒序） |
| GET | `/api/runs/{id}` | meta + 实时 status + elapsed |
| GET | `/api/runs/{id}/events?offset=N` | `{status, events[offset:]}` |
| GET | `/api/runs/{id}/articles` | 文章列表 `[{idx, topic, file}]` |
| GET | `/api/runs/{id}/articles/{idx}` | `{content}` |
| PUT | `/api/runs/{id}/articles/{idx}` | body `{"content": ...}`，覆盖写回 |
| GET | `/api/runs/{id}/raw` | `{content}` 原始输出 |

### 8. 前端 `static/index.html`

单文件单页，原生 JS + marked.js 渲染，无框架。直接消费上表 API：轮询 events 做日志面板、加载文章列表、源码/预览切换编辑、复制/下载。改后端接口时要同步这里的 fetch 路径与字段。

### 9. 配置与部署

- `.env`：`COZE_API_TOKEN` / `COZE_WORKFLOW_ID`（已 gitignore，模板见 `.env.example`）。`main.py` 与 `app.py` 都 `load_dotenv()`。
- `DATA_DIR` 环境变量可覆盖运行数据目录——改存储位置前先确认当前值（曾因 `KEY:value` 冒号写法导致静默为空）。
- 部署到 ModelScope Docker 创空间（私有），容器端口 7860，密钥走空间环境变量，云端数据在 `/mnt/workspace/hot_news2post/runs`。
- `deploy_pkg/` 是部署暂存目录，内容与根目录的 `main.py`/`app.py`/`coze_client.py`/`parser.py`/`store.py`/`requirements.txt`/`Dockerfile`/`static/` 一一对应。**改了这些文件要同步到 `deploy_pkg/`**；新增根目录模块时记得 Dockerfile 与 deploy_pkg 都要加。

## 改动时的注意点

- 加模块 = 根目录新增 `.py` + import 用顶层模块名（如 `from parser import ...`），不要建包、不要改成 `from app.parser import ...`。
- 改解析协议或新增 Golden 样例 → 同步 `tests/test_parser.py`，跑 `pytest` 全绿。
- 改 API → 同步 `static/index.html` 的 fetch 调用。
- 改部署相关文件 → 同步 `deploy_pkg/` 与 `Dockerfile` 的 `COPY` 列表。
- 设计背景与决策记录见 `design.md`（D1 多平台分组协议 / D2 事件透传 / D3 直接覆盖；双视角评估在第 5 节）。以扁平布局为准。
