"""解析 Coze 工作流输出。

2026-11-18 新协议（当前主路径）：返回按平台分组的对象
  {"X_output":[...], "gzh_output":[...], "lkdn_output":[...], "wb_output":[...], "xhs_output":[...]}
  - 每个数组是某平台下的一篇或多篇适配初稿（整段 markdown 字符串）
  - 平台下篇数可不齐；每篇标题用它自己的标题行，不做跨平台对齐
  - 拒答元素过滤不入库

旧协议（兼容回退）：单数组 {"output":[...]} 或裸数组，每元素一篇初稿。

返回结构：
  {"ok", "platforms":[{key,name,count,skipped}], "articles":[{idx,platform,platform_name,topic,draft}],
   "raw", "error", "skipped", "normalized"}
  articles 的 idx 是跨平台全局序号，store 据此落盘 articles/{idx:02d}.md。
"""
import json
import re

# 平台 key -> 显示名（顺序即输出顺序）
PLATFORM_NAMES = {
    "X_output": "X（推特）",
    "gzh_output": "微信公众号",
    "lkdn_output": "LinkedIn",
    "wb_output": "微博",
    "xhs_output": "小红书",
}
_TOPIC_KEYS = ("topic", "title", "name", "hot_topic", "话题", "标题")
_DRAFT_KEYS = ("draft", "article", "content", "output", "text", "初稿", "正文", "文章")
# 拒答特征：命中任一且无《标题》即视为 workflow 拒绝创作，不入库
_REJECT_MARKERS = (
    "无法为你创作", "无法产出", "无法按照要求创作", "无法完成",
    "请您提供具体的财经热点", "不符合财经类内容创作要求", "不符合财经",
    "不属于财经热点范畴", "未涉及金融、投资、宏观经济",
)
_BOOK_TITLE_RE = re.compile(r"《([^》]{2,80})》")
# 结构规范化：workflow 偶尔把「标题及推荐理由」排在「文案初稿」之前。
_TITLE_SECTION_RE = re.compile(r"^#{1,6}\s*.*(?:标题及推荐理由|标题及理由|标题推荐)", re.MULTILINE)
_DRAFT_SECTION_RE = re.compile(r"^#{1,6}\s*.*文案初稿", re.MULTILINE)
# 标题行标记：必须是行首的「标题/主标题/推荐英文标题/微博标题/title」等，
# 不能是括号注释里的「标题+正文」「结构保持：标题→」（真实输出踩过）
# 英文 Title 也纳入（X 平台输出「#### Title (X-style...)」）
_TITLE_LINE_RE = re.compile(r"^#{0,6}\s*\*{0,2}[\u4e00-\u9fa5A-Za-z]{0,4}(?:主)?(?:标题|title(?![A-Za-z]))", re.I)
_BACKTICK_RE = re.compile(r"`([^`]{6,120})`")


def parse_output(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        return _empty(raw, "输出为空")
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return _empty(raw, f"JSON 解析失败: {e}")
    # 裸数组：旧单数组协议
    if isinstance(data, list):
        return _parse_legacy(data, raw)
    if not isinstance(data, dict):
        return _empty(raw, f"期望平台对象或数组，得到 {type(data).__name__}")
    # 判定多平台协议：key 命中已知平台或以 _output 结尾
    if any(k in PLATFORM_NAMES or k.endswith("_output") for k in data):
        return _parse_multi(data, raw)
    # 兼容旧协议：包装层里找数组
    for key in ("data", "output", "articles", "result", "list"):
        if isinstance(data.get(key), list):
            return _parse_legacy(data[key], raw)
    return _empty(raw, "未识别的输出结构（既非平台对象也非单数组）")


def _empty(raw, error):
    return {"ok": False, "platforms": [], "articles": [], "raw": raw,
            "error": error, "skipped": 0, "normalized": 0}


def _parse_multi(data: dict, raw: str) -> dict:
    platforms, flat = [], []
    total_skipped = total_norm = global_idx = 0
    for key, arr in data.items():
        if not isinstance(arr, list):
            continue
        name = PLATFORM_NAMES.get(key, key.replace("_output", "").upper())
        plat_skipped = 0
        plat_articles = []
        for item in arr:
            if _is_rejection(item):
                plat_skipped += 1
                total_skipped += 1
                continue
            topic, draft = _extract(item, len(plat_articles) + 1)
            draft, changed = _normalize_structure(draft)
            if changed:
                total_norm += 1
            global_idx += 1
            art = {"idx": global_idx, "platform": key, "platform_name": name,
                   "topic": topic, "draft": draft}
            plat_articles.append(art)
            flat.append(art)
        platforms.append({"key": key, "name": name,
                          "count": len(plat_articles), "skipped": plat_skipped})
    if not platforms:
        return _empty(raw, "平台对象里没有有效数组")
    return {"ok": True, "platforms": platforms, "articles": flat, "raw": raw,
            "error": None, "skipped": total_skipped, "normalized": total_norm}


def _parse_legacy(items: list, raw: str) -> dict:
    """旧单数组协议：归入一个默认平台，保持下游结构一致。"""
    flat, skipped, normalized = [], 0, 0
    for i, item in enumerate(items, 1):
        if _is_rejection(item):
            skipped += 1
            continue
        topic, draft = _extract(item, i)
        draft, changed = _normalize_structure(draft)
        if changed:
            normalized += 1
        flat.append({"idx": len(flat) + 1, "platform": "legacy",
                     "platform_name": "初稿", "topic": topic, "draft": draft})
    return {"ok": True,
            "platforms": [{"key": "legacy", "name": "初稿", "count": len(flat), "skipped": skipped}],
            "articles": flat, "raw": raw, "error": None,
            "skipped": skipped, "normalized": normalized}


def _normalize_structure(draft: str) -> tuple[str, bool]:
    """标题段在前时交换为「初稿在前 → 标题在后」。保守策略，只换顺序不改内容。"""
    lines = draft.splitlines()
    title_i = next((i for i, l in enumerate(lines) if _TITLE_SECTION_RE.match(l)), None)
    draft_i = next((i for i, l in enumerate(lines) if _DRAFT_SECTION_RE.match(l)), None)
    if title_i is None or draft_i is None or title_i > draft_i:
        return draft, False
    sep_i = next((i for i in range(title_i + 1, draft_i)
                  if lines[i].strip() == "---"), None)
    if sep_i is None:
        return draft, False
    head = lines[:title_i]
    title_block = lines[title_i:sep_i]
    draft_block = lines[draft_i:]
    tail = [x for x in lines[sep_i + 1:draft_i] if x.strip()]
    new_lines = head + draft_block + ["", "---", ""] + title_block + tail
    return "\n".join(new_lines).rstrip() + "\n", True


def _is_rejection(item) -> bool:
    """拒答文案：无《标题》且命中拒答措辞；对象形态不按拒答处理。"""
    if not isinstance(item, str):
        return False
    s = item.strip()
    return not _BOOK_TITLE_RE.search(s) and any(m in s for m in _REJECT_MARKERS)


def _title_of(s: str, index: int) -> str:
    """提取一篇的标题。

    真实多平台输出里标题标记形态各异：`**标题**：X`、`#### 标题：X`、
    `#### 标题（注释）：《X》`、`#### 标题（注释）\\n《X》`、`#### 推荐英文标题（...）\\n1.【风格】X`。
    关键坑：括号注释里可能自带冒号（如「标题（LinkedIn风格：专业精准，突出商务价值）」），
    旧正则会误把冒号后的「专业精准，突出商务价值）」当标题。这里改为先锁定含「标题」的行，
    再优先取该行/随后几行的《》或反引号内容，最后才取去括号注释后的纯文本。
    """
    # 旧协议：锁定标题推荐段，避免误取正文引用书名号
    m_sec = _TITLE_SECTION_RE.search(s)
    if m_sec:
        bm = _BOOK_TITLE_RE.search(s, m_sec.end())
        if bm:
            return bm.group(1).strip()

    lines = s.splitlines()
    for i, line in enumerate(lines[:30]):
        t = line.strip()
        # 只认行首的标题标记，跳过「（标题+正文，…）」「结构保持：标题→…」等说明行
        m_line = _TITLE_LINE_RE.match(t)
        if not m_line:
            continue
        rest = t[m_line.end():]
        # 1) 同行《标题》
        bm = _BOOK_TITLE_RE.search(rest)
        if bm and len(bm.group(1).strip()) > 2:
            return bm.group(1).strip()
        # 2) 同行反引号英文标题（跳过话题标签）
        bt = _BACKTICK_RE.search(rest)
        if bt and not bt.group(1).lstrip().startswith("#"):
            return bt.group(1).strip()[:90]
        # 3) 去掉括号注释后剩下的纯文本
        cleaned = re.sub(r"[（(][^）)]*[）)]", "", rest).strip(" ：:*`")
        if len(cleaned) > 3 and "→" not in cleaned:
            return cleaned[:90]
        # 4) 标题落在随后几行（《》/反引号/纯文本）
        for nxt in lines[i + 1:i + 5]:
            n = nxt.strip()
            if not n:
                continue
            bm2 = _BOOK_TITLE_RE.search(n)
            if bm2:
                return bm2.group(1).strip()
            bt2 = _BACKTICK_RE.search(n)
            if bt2 and not bt2.group(1).lstrip().startswith("#"):
                return bt2.group(1).strip()[:90]
            # 微博「#话题# + 标题文字」形态：取话题标签后的文字作标题（BC-005）
            hm = re.match(r"^#[^#]+#\s*(.+)$", n)
            if hm and len(hm.group(1).strip()) > 3:
                return hm.group(1).strip()[:90]
            if not n.startswith("#") and len(n) > 8 and "标题" not in n:
                c = re.sub(r"^\d+[\.、]\s*", "", n)
                c = re.sub(r"^【[^】]*】\s*", "", c)
                c = c.strip(" *：`")
                if c:
                    return c[:90]
    # 回退 1：【标题】/【Title】方括号标注（小红书常见）
    for line in lines[:30]:
        t = line.strip()
        m = re.match(r"^[【\[]\s*(?:标题|title)\s*[】\]]\s*[:：]?\s*(.+)$", t, re.I)
        if m:
            c = m.group(1).strip().strip("*` ")
            if c:
                return c[:90]
    # 回退 2：markdown 标题行（跳过 --- 分隔符和「结构说明型」标题）
    # 元素开头常是「### 微信公众号长文」「### 一、改写后文案」这类结构说明，
    # 真正标题在其后一行（如「# 中俄美确立伙伴关系…」），需跳过说明继续找。
    _STRUCT_META = ("长文", "文案", "适配", "改写", "版本", "标题", "titles", "article",
                    "结构", "说明", "提示词", "prompt", "图文", "竖版", "全文",
                    "introduction", "logic", "takeaway", "chain", "parallel", "evidence",
                    "热点引入", "核心逻辑", "观点总结", "正文", "结语", "开头", "结尾", "正文结构")
    first_struct = None  # 兜底用：第一个被跳过的结构说明标题
    for idx2, line in enumerate(lines[:30]):
        i = idx2  # 修正：本循环的索引，避免复用主循环遗留的 i
        t = line.strip()
        if t in ("---", "***", "___") or not t:
            continue
        # 话题标签行不作 markdown 标题（BC-004：文末「#标签1 #标签2」曾被误取为标题）
        if t.startswith("#") and (re.match(r"^#\S+\s+#", t) or re.match(r"^#[^#]+#$", t)):
            continue
        m = re.match(r"^#{1,6}\s*(.+)$", t)
        if m:
            c = m.group(1).strip()
            bm = _BOOK_TITLE_RE.search(c)
            if bm:
                return bm.group(1).strip()
            c = re.sub(r"[（(][^）)]*[）)]", "", c).strip().strip("*` ")
            if not c or len(c) <= 3:
                continue
            # 结构说明型标题：纯序号开头，或含元词（长文/文案/Titles/Introduction…）
            is_struct = bool(re.match(r"^[一二三四五六七八九十]+[、.．]", c)) or \
                any(meta in c.lower() for meta in _STRUCT_META)
            if is_struct:
                if first_struct is None:
                    first_struct = c
                # 结构说明行带「：」后缀时，冒号后即真标题（如「### 微信公众号长文：XXX」）
                # 括号注释内的冒号已在上面被去掉，此处剩的是行本身的冒号
                colon = re.search(r"[：:]\s*(.+)$", c)
                if colon and len(colon.group(1).strip()) > 3:
                    return colon.group(1).strip()[:90]
                # 结构说明后的标题：优先取 markdown 标题行（如 gzh 的「# 中俄美确立…」），
                # 否则取第一行实质正文 hook（小红书把标题融进开篇句）
                for nxt in lines[i + 1:i + 4]:
                    n = nxt.strip()
                    if not n or n in ("---", "***", "___"):
                        continue
                    # 微博单话题标签行 #标题# → 去首尾 # 作为标题（BC-004）
                    sm = re.match(r"^#([^#]+)#$", n)
                    if sm and len(sm.group(1).strip()) > 3:
                        return sm.group(1).strip()[:90]
                    # markdown 标题行：去 # 后若是结构说明则跳过，否则就是真标题
                    mh = re.match(r"^#{1,6}\s*(.+)$", n)
                    if mh:
                        hc = re.sub(r"[（(][^）)]*[）)]", "", mh.group(1)).strip().strip("*` ")
                        if hc and len(hc) > 3 and not any(meta in hc.lower() for meta in _STRUCT_META) \
                                and not re.match(r"^[一二三四五六七八九十]+[、.．]", hc):
                            return hc[:90]
                        continue
                    if re.match(r"^[【\[]\s*(?:标题|title)", n, re.I):
                        continue
                    if re.match(r"^【[^】]*标题[^】]*】", n):
                        continue
                    if re.match(r"^#\S+\s+#\S+", n):  # 话题标签行
                        continue
                    m_style = re.match(r"^\d+[\.、]\s*\*\*[^*]+\*\*\s*[:：]\s*\*([^*]+)\*", n)
                    if m_style:
                        return m_style.group(1).strip()[:90]  # 英文标题模式
                    if re.match(r"^[✅❌💡🔹📊🤔🔥][^\n]{0,8}[：:]", n):  # 段落小标题
                        continue
                    return n[:90]
                continue
            return c[:90]
    # 回退 2.5：英文「**风格**: *标题*」模式（LinkedIn 3 版本标题）
    for line in lines[:30]:
        m = re.match(r"^\d+[\.、]\s*\*\*[^*]+\*\*\s*[:：]\s*\*([^*]+)\*", line.strip())
        if m:
            return m.group(1).strip()[:90]
    # 回退 3：全文《》 → 反引号英文标题
    bm = _BOOK_TITLE_RE.search(s)
    if bm:
        return bm.group(1).strip()
    for b in _BACKTICK_RE.findall(s):
        if not b.lstrip().startswith("#") and len(b) > 6:
            return b.strip()[:90]
    # 回退 4：结构说明标题后的第一行 hook 正文（小红书把标题融进开篇句）
    if first_struct:
        for j, line in enumerate(lines):
            if first_struct in line:
                for nxt in lines[j + 1:j + 5]:
                    n = nxt.strip()
                    if not n or n in ("---", "***", "___") or n.startswith("#"):
                        continue
                    if re.match(r"^【[^】]*标题[^】]*】", n):
                        continue
                    if re.match(r"^#\S+\s+#\S+", n):  # 话题标签行
                        continue
                    if re.match(r"^[✅❌💡🔹📊🤔🔥][^\n]{0,8}[：:]", n):  # 段落小标题
                        continue
                    return n[:90]
                break
        return first_struct[:90]
    # 回退 5：首个非空、非分隔符、非话题标签行
    for line in lines:
        t = line.strip().lstrip("#").strip()
        if t and t not in ("---", "***", "___") and not t.startswith("#"):
            return t.strip("*` ")[:90]
    return f"话题{index}"


def _extract(item, index: int):
    if isinstance(item, str):
        return _title_of(item.strip(), index), item.strip()
    if isinstance(item, dict):
        topic = next((str(item[k]).strip() for k in _TOPIC_KEYS
                      if item.get(k) and str(item[k]).strip()), f"话题{index}")
        draft = next((str(item[k]).strip() for k in _DRAFT_KEYS
                      if item.get(k) and str(item[k]).strip()), None)
        if draft is None:
            rest = {k: v for k, v in item.items() if k not in _TOPIC_KEYS}
            draft = json.dumps(rest, ensure_ascii=False, indent=2) if rest else json.dumps(item, ensure_ascii=False)
        return topic, draft
    return f"话题{index}", str(item)
