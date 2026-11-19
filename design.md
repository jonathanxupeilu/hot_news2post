# 财经自媒体文章工作台 — 设计文档

> 状态：待确认 | 日期：2026-11-16

## 1. 目标与价值

**一句话**：打开本地工作台 → 一键触发 Coze 工作流（抓新闻 → 选热点 → 知识库匹配分析框架 → 写初稿）→ 工作台内查看进度、编辑初稿、导出 Markdown。

- 用户：单人（财经自媒体作者），日更场景。
- 价值：把「打开 Coze 网页 → 手动运行 → 复制结果 → 本地编辑」压缩成一站式操作。
- 非目标（MVP 明确不做）：自动发布、定时任务、多账号、团队协作。

## 2. 架构

```
浏览器 (单页) ──HTTP──> FastAPI (localhost:8000)
                          │  .env 读 COZE_API_TOKEN / COZE_WORKFLOW_ID
                          ▼
                   coze.cn OpenAPI (SSE 流式)
                          │
                          ▼
                   data/runs/<timestamp>/  (运行记录 JSON + 初稿 .md)
```

- 后端：Python 3.11+ / FastAPI / httpx（SSE 解析）。
- 前端：单 HTML 页面（原生 JS + marked.js 渲染），不引入框架。
- Coze 接入：优先官方 SDK（cozing/pycoze，接入时验证可用版本），SDK 不可用则回退 httpx 直连 SSE。

## 3. MVP 功能（P0）

1. **触发运行**：点按钮 → 调 Coze 工作流（stream=true），实时展示节点/消息流。
2. **初稿落盘**：解析工作流返回的**按平台分组的对象**（`{"X_output":[...],"gzh_output":[...],...}`，每个数组是该平台的一篇或多篇适配初稿），存 `data/runs/<ts>/articles/NN.md`（NN 为跨平台全局序号）+ 元数据 JSON（含 `platforms` 平台清单与每篇的 platform 字段）。
3. **草稿列表**：按时间倒序展示历史运行，点击加载；文章区按平台 Tab 分类展示（X / 公众号 / LinkedIn / 微博 / 小红书），保持卡片网格布局。
4. **编辑器**：Markdown 源码编辑 + 渲染预览，可切换；保存写回文件。
5. **导出**：复制到剪贴板 / 下载 .md。

P1（不做，留接口）：定时触发、公众号草稿箱推送、多工作流切换。

## 4. 关键设计决策

### D1 工作流输出解析（多平台分组协议，当前主路径）
- **2026-11-18 三次更新后的形态（当前）**：workflow 返回**按平台分组的对象**——`{"X_output":[...], "gzh_output":[...], "lkdn_output":[...], "wb_output":[...], "xhs_output":[...]}`，每个数组是某平台风格与调性适配的一篇或多篇初稿（整段 markdown 字符串）。平台 key → 显示名见 `parser.py:PLATFORM_NAMES`；未知 `*_output` key 也照常解析。
- **组织维度：平台为主**（2026-11-18 确认）。前端按平台 Tab 分类展示，不做跨平台话题对齐——各平台标题格式高度异质（X 英文反引号 / 公众号《》/ 微博话题标签 / 小红书 emoji 标题），对齐不可靠。每篇用自己的标题，标题提取优先级：标题推荐段《》→「标题/主标题：」标注 → 全文《》→ 反引号英文标题 → 首行；无标题回退「话题N」。
- **存储**：`parse_output` 返回 `platforms`（平台清单）+ `articles`（扁平，每篇带 `idx` 全局序号、`platform`、`platform_name`）。store 按全局 idx 落盘 `articles/NN.md`，meta 存 platforms 与每篇 platform 字段；API `/articles` 返回 `{platforms, articles}`，旧 run 自动从 articles 推导平台清单。
- **历史形态（兼容回退）**：单数组 `{"output":[...]}` 或裸数组、包装层 `{"data":[...]}` 仍可解析，归入默认平台「初稿」。字符串/对象元素、markdown 代码围栏均兼容。
- **拒答过滤**：命中「未涉及金融/无法创作/不符合财经…」且无《标题》的元素不入库；按平台与总计写入 `meta.skipped`，供归因。
- **结构规范化（兜底）**：workflow 偶发把「标题及推荐理由」排在「文案初稿」之前（早期 lululemon 篇）。解析器保守识别——两个段标题都在、标题在前、中间有 `---` 才交换；重排数写入 `meta.normalized`。`raw.txt` 保留原文可溯源，只改落盘 draft。workflow 二次修复后实测已统一为「初稿在前」（`normalized=0`），兜底保留作安全网。
- 解析失败时整段存 `raw.txt` + meta 标记 parse_error，不丢内容。
- **工程约束**：新 workflow 输出量约为旧版 5 倍，同步 JSON 返回耗时可能超 600s，`coze_client.py` 默认超时已调至 **1800s**。
- **算法视角**：解析正确率是第一道质量门禁；Golden 样例 = 工作流真实输出（多平台分组 + 拒答 + 结构异类），`tests/test_parser.py`（20 项）全部通过才算接通。口径变动须先确认再改解析器。

### D2 运行状态可见性
- Coze SSE 返回的事件流（message/step/done/error）原样透传到前端日志面板，不加工不隐藏——可观测、可归因。
- 超时：单次运行上限 10 分钟，前端显示已耗时。

### D3 编辑保存策略
- 直接覆盖原文件，不做版本树（单人使用，git 管项目但 data/ 入 gitignore 前**不 ignore**，git 即版本史）。

## 5. 双视角评估

**AI 产品经理视角**：
- ROI：工作台本身无智能，价值全在「降低使用摩擦 + 沉淀历史初稿」。MVP 2 天内可交付，先跑通质量再谈自动化。
- 验收口径：真实 Token + 真实工作流跑通全链路；从点击到初稿可编辑 ≤ 一杯咖啡时间（工作流执行时间除外）。
- 风险：工作流输出格式若在 Coze 侧变更，解析会静默失败 → 用 D1 告警兜底，不做静默降级。

**算法工程师视角**：
- 口径先行：初稿质量的评判不在此项目内（工作流侧的评测集），但工作台需留存每次运行的原始输入输出，供后续消融与 Bad Case 回流。
- 可复现：每次运行记录 workflow_id、时间戳、原始字符串、解析结果，四元组完整即可复现当时的解析行为。
- 失败模式：SSE 中断、Token 失效、解析分不出字段——三种都必须有显式错误态，不吞异常。

## 6. 目录结构（扁平布局）

所有 Python 模块都在项目根目录，不是 `app/` 包。原因：ModelScope 创空间的网页上传器会拍平文件夹结构（`app/` 包上传即散），因此刻意不建子目录包。新增模块一律放根目录、用顶层模块名 import（如 `from parser import parse_output`）。

```
hot_news2post/
├── main.py              # FastAPI 应用主体：路由 + 后台线程编排
├── app.py               # 创空间部署入口：设定 DATA_DIR 后以 0.0.0.0:7860 启动 main:app
├── coze_client.py       # coze.cn 工作流客户端（SSE / JSON 双模式）
├── parser.py            # 工作流输出解析（多平台分组协议，兼容旧单数组）
├── store.py             # RunStore：运行记录/草稿的文件读写
├── static/index.html    # 单页前端（原生 JS + marked.js）
├── data/runs/           # 运行记录 + 草稿（本地默认目录，可被 DATA_DIR 覆盖）
├── tests/test_parser.py # pytest（Golden 样例）
├── deploy_pkg/          # 创空间部署暂存目录，与根目录核心文件一一对应
├── requirements.txt / Dockerfile / .env.example / .gitignore
├── .env                 # COZE_API_TOKEN / COZE_WORKFLOW_ID（gitignore）
└── design.md
```

> 注：早期设计稿曾计划用 `app/` 子包，已废弃，以上扁平结构为准。改核心文件时须同步 `deploy_pkg/` 与 `Dockerfile` 的 `COPY` 列表。
