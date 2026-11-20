import json

from parser import parse_output

# Golden 样例：与 Coze 工作流真实输出形态对齐后持续补充


def test_array_of_strings():
    raw = json.dumps(["# 降息预期升温\n\n正文A……", "# 黄金创新高\n\n正文B……"],
                     ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and len(r["articles"]) == 2
    assert r["articles"][0]["topic"] == "降息预期升温"
    assert r["articles"][1]["draft"].startswith("# 黄金创新高")
    assert "正文B" in r["articles"][1]["draft"]


def test_array_of_objects():
    raw = json.dumps([
        {"topic": "CPI 公布", "draft": "正文1"},
        {"title": "央行操作", "article": "正文2"},
    ], ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and r["articles"][0]["topic"] == "CPI 公布"
    assert r["articles"][1]["topic"] == "央行操作"
    assert r["articles"][1]["draft"] == "正文2"


def test_wrapped_dict():
    raw = json.dumps({"data": [{"topic": "T1", "draft": "D1"}]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and r["articles"][0]["topic"] == "T1"


def test_markdown_fence():
    raw = "```json\n" + json.dumps([{"topic": "T", "draft": "D"}], ensure_ascii=False) + "\n```"
    r = parse_output(raw)
    assert r["ok"] and r["articles"][0]["draft"] == "D"


def test_string_item_without_title():
    # 无《标题》/标题标注时，回退首个非空行作为标题（新协议要求每篇用自身标题）
    raw = json.dumps(["无标题正文"], ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and r["articles"][0]["topic"] == "无标题正文"


def test_string_item_with_h3_title():
    raw = json.dumps(["### 一线城市房价拐点？\n\n正文……"], ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and r["articles"][0]["topic"] == "一线城市房价拐点？"


def test_invalid_json_keeps_raw():
    raw = "这不是 JSON 输出"
    r = parse_output(raw)
    assert not r["ok"] and r["raw"] == raw and r["error"]


def test_empty_output():
    r = parse_output("")
    assert not r["ok"] and r["error"] == "输出为空"


# ---- 2026-11-18 workflow 重新发布后的真实输出形态（Golden 样例）----
# 新元素：整段字符串，含《标题》+ 文案；话题取第一个《标题》，拒答过滤


def test_new_protocol_topic_from_book_title():
    article = (
        "### 一、3个不同角度的标题及推荐理由\n"
        "#### 标题1（数据式·投资维度）：《英伟达狂赚653亿！营收暴增103%，全球科技股投资配置要重构？》\n"
        "推荐理由：直接抛出核心财报数据。\n\n"
        "### 二、金融分析文案初稿\n"
        "【热点引入】11月17日英伟达公布季度财报，均超市场预期。\n"
    )
    raw = json.dumps({"output": [article]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and len(r["articles"]) == 1
    assert r["articles"][0]["topic"] == "英伟达狂赚653亿！营收暴增103%，全球科技股投资配置要重构？"
    assert "金融分析文案初稿" in r["articles"][0]["draft"]
    assert r["skipped"] == 0


def test_new_protocol_filters_rejections():
    article = (
        "### 一、3个不同角度的标题及推荐理由\n"
        "#### 标题1：《黄金7天狂跌88元，年后咋了？》\n"
        "### 二、财经分析文案初稿\n正文……\n"
    )
    rejection = (
        "该热点为纯文艺演出类娱乐事件，未涉及金融、投资、宏观经济等任何"
        "指定财经维度，不符合财经类创作的要求，因此无法为你创作符合规范的财经分析文案及标题。"
    )
    raw = json.dumps({"output": [article, rejection]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and len(r["articles"]) == 1
    assert r["articles"][0]["topic"] == "黄金7天狂跌88元，年后咋了？"
    assert r["skipped"] == 1


def test_new_protocol_all_rejected():
    rejection = "请您提供具体的财经热点话题内容，我才能为您创作吸引眼球的标题及相关财经分析文案哦。"
    raw = json.dumps({"output": [rejection]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and r["articles"] == [] and r["skipped"] == 1


def test_structure_normalized_when_title_first():
    # 异类结构：标题及推荐理由在前，文案初稿在末尾（workflow 偶发）
    article = (
        "### 一、3个不同角度的标题及推荐理由\n"
        "#### 1. 冲突式标题：《Lululemon翻包事件：信任危机会影响投资价值吗？》\n"
        "推荐理由：紧扣热点。\n"
        "---\n"
        "### 二、财经分析文案初稿\n"
        "【热点引入】11月13日北京门店事件。\n"
        "【核心逻辑】消费者信任与板块投资逻辑。\n"
    )
    raw = json.dumps({"output": [article]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and r["normalized"] == 1
    draft = r["articles"][0]["draft"]
    # 重排后初稿在前、标题在后，且两段内容完整无丢失
    assert draft.index("财经分析文案初稿") < draft.index("标题及推荐理由")
    assert "【核心逻辑】消费者信任与板块投资逻辑。" in draft
    assert "Lululemon翻包事件" in draft


def test_topic_skips_reference_booktitle_in_draft():
    # 初稿正文引用《华尔街日报》，真正话题在标题段——不应误取引用
    article = (
        "### 技能1：财经分析文案初稿\n"
        "**热点引入**：据《华尔街日报》报道，原油市场波动。\n"
        "**核心逻辑**：基金持仓变化。\n"
        "---\n"
        "### 技能2：3个标题及推荐理由\n"
        "#### 标题1：《对伊战争烧到美海军！原油、黄金、美债要变天？》\n"
        "推荐理由：紧扣热点。\n"
    )
    raw = json.dumps({"output": [article]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"]
    assert r["articles"][0]["topic"] == "对伊战争烧到美海军！原油、黄金、美债要变天？"
    assert "华尔街日报" not in r["articles"][0]["topic"]


def test_structure_already_normal_untouched():
    # 正常结构（初稿在前）不应被改动
    article = (
        "### 技能1：金融分析文案初稿\n"
        "**热点引入**：财报超预期。\n"
        "---\n"
        "### 技能2：3个标题及推荐理由\n"
        "#### 标题1：《...》\n"
    )
    raw = json.dumps({"output": [article]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and r["normalized"] == 0
    assert r["articles"][0]["draft"].startswith("### 技能1：金融分析文案初稿")


# ---- 2026-11-18 二次更新：多平台分组协议（当前主路径）----
# 输出为按平台分组的对象 {"X_output":[...], "gzh_output":[...], ...}


def _multi_raw():
    return json.dumps({
        "X_output": [
            "### 适配X平台的内容输出\n#### X主标题\n1. **`APEC Shenzhen Run-Ins = Trade Windfall?`**\n\n正文X1",
            "#### 标题1：`Is Israel Seizing More Land?` #Geopolitics\n正文X2",
        ],
        "gzh_output": [
            "# 《APEC深圳街头彩蛋，藏着区域经贸新风口？》\n（适配公众号长文）\n正文G1",
        ],
        "wb_output": [
            "### 微博标题\n《APEC街头彩蛋！澳泰偶遇藏新风口？》#APEC\n正文W1",
        ],
    }, ensure_ascii=False)


def test_multi_platform_basic():
    r = parse_output(_multi_raw())
    assert r["ok"] and r["error"] is None
    assert len(r["platforms"]) == 3
    keys = [p["key"] for p in r["platforms"]]
    assert keys == ["X_output", "gzh_output", "wb_output"]
    assert len(r["articles"]) == 4
    # 全局 idx 连续
    assert [a["idx"] for a in r["articles"]] == [1, 2, 3, 4]


def test_multi_platform_counts_and_names():
    r = parse_output(_multi_raw())
    x = r["platforms"][0]
    assert x["name"] == "X（推特）" and x["count"] == 2
    gzh = r["platforms"][1]
    assert gzh["name"] == "微信公众号" and gzh["count"] == 1


def test_multi_platform_platform_field():
    r = parse_output(_multi_raw())
    by_idx = {a["idx"]: a for a in r["articles"]}
    assert by_idx[1]["platform"] == "X_output"
    assert by_idx[1]["platform_name"] == "X（推特）"
    assert by_idx[3]["platform"] == "gzh_output"


def test_multi_platform_topics():
    r = parse_output(_multi_raw())
    topics = [a["topic"] for a in r["articles"]]
    assert topics[0] == "APEC Shenzhen Run-Ins = Trade Windfall?"
    assert topics[1] == "Is Israel Seizing More Land?"
    assert topics[2] == "APEC深圳街头彩蛋，藏着区域经贸新风口？"
    assert topics[3] == "APEC街头彩蛋！澳泰偶遇藏新风口？"


def test_multi_platform_filters_rejection():
    raw = json.dumps({
        "X_output": [
            "无法为你创作符合规范的财经分析文案，未提供具体热点。",
            "#### 标题：`Valid English Title Here`\n正文",
        ],
        "wb_output": [],
    }, ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and len(r["articles"]) == 1
    assert r["skipped"] == 1
    assert r["platforms"][0]["count"] == 1 and r["platforms"][0]["skipped"] == 1
    assert r["platforms"][1]["count"] == 0  # 空平台仍保留占位


def test_multi_platform_unknown_key_still_parsed():
    raw = json.dumps({"toutiao_output": ["#### 标题：《今日头条话题》\n正文"]},
                     ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and len(r["platforms"]) == 1
    assert r["platforms"][0]["count"] == 1
    assert r["articles"][0]["topic"] == "今日头条话题"


# ---- 真实多平台输出暴露的标题提取边界（2026-11-19 实跑）----


def test_title_not_confused_by_colon_in_parenthetical_note():
    # 括号注释自带冒号，真正标题在下一行《》
    art = ("### 适配LinkedIn的专业版文章\n"
           "#### 标题（LinkedIn风格：专业精准，突出商务价值）\n"
           "《APEC深圳会议的财经洞察：从晚宴元素看大湾区的战略机遇》\n"
           "---\n正文")
    raw = json.dumps({"X_output": [], "lkdn_output": [art]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["articles"][0]["topic"] == "APEC深圳会议的财经洞察：从晚宴元素看大湾区的战略机遇"


def test_title_skips_descriptive_line_with_arrow():
    # 说明行含「标题→热点引入」应跳过，取后面的 **标题**：
    art = ("### 适配X的3组内容（结构保持：标题→热点引入→核心逻辑→观点总结）\n"
           "---\n"
           "#### 【版本1：观点式适配】\n"
           "**标题**：APEC Dinner’s Cantonese Clues: 3 Key Finance Wins for GBA\n"
           "正文")
    raw = json.dumps({"X_output": [art]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["articles"][0]["topic"] == "APEC Dinner’s Cantonese Clues: 3 Key Finance Wins for GBA"


def test_title_bold_label_with_colon():
    art = "**标题**：Zhejiang’s 9,000 RMB Penalty: The Hidden Impact on Business\n正文"
    raw = json.dumps({"lkdn_output": [art]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["articles"][0]["topic"] == "Zhejiang’s 9,000 RMB Penalty: The Hidden Impact on Business"


def test_title_recommended_label_then_style_prefix():
    art = ("### 适配X（英文平台）的内容输出\n"
           "#### 推荐英文标题（对应原3种逻辑）\n"
           "1. 【悬念式】Zhejiang Firm Fined 9k Yuan! Why Signal Jammers Matter\n"
           "正文")
    raw = json.dumps({"X_output": [art]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["articles"][0]["topic"] == "Zhejiang Firm Fined 9k Yuan! Why Signal Jammers Matter"


def test_title_ignores_balanced_note_containing_title_word():
    # 说明行「（标题+正文，贴合LinkedIn职场受众：…）」不该被当成标题，
    # 真正标题在下一行「标题（观点式…）：《AI for IT Ops…》」
    art = ("### LinkedIn适配版内容（标题+正文，贴合LinkedIn职场受众：猎头、投资者）\n"
           "#### 标题（观点式，精准触达LinkedIn核心受众）：《AI for IT Ops Isn’t Layoffs—It’s a New Talent Signal》\n"
           "---\n正文")
    raw = json.dumps({"lkdn_output": [art]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["articles"][0]["topic"] == "AI for IT Ops Isn’t Layoffs—It’s a New Talent Signal"


def test_no_title_marker_falls_back_to_first_heading():
    # 无标题标记时取开篇 hook（不取「微博正文」这类结构说明）
    art = "### 微博正文（含结构对应+短句风格）\n🔥快消圈两大关键信号！\n正文"
    raw = json.dumps({"wb_output": [art]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["articles"][0]["topic"] == "🔥快消圈两大关键信号！"


# ---- 2026-11-19 二次优化的真实输出形态（标题提取边界）----


def test_title_from_after_separator():
    # 元素以 --- 开头，真正标题在后面的 markdown 标题行
    art = ("---\n"
           "### 微信公众号长文\n"
           "# 中俄美确立伙伴关系：全球投资的「和平红利」正在释放？\n"
           "各位关注全球投资的小伙伴们…")
    raw = json.dumps({"gzh_output": [art]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["articles"][0]["topic"] == "中俄美确立伙伴关系：全球投资的「和平红利」正在释放？"


def test_title_from_bracket_label():
    # 【标题】标注（小红书）
    art = ("### 改写后的小红书风格商业&投资文案\n"
           "【标题】中俄美深圳会谈喊\"解决全球问题\"？投资者先盯这2个细节😱\n"
           "正文：…")
    raw = json.dumps({"xhs_output": [art]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["articles"][0]["topic"] == "中俄美深圳会谈喊\"解决全球问题\"？投资者先盯这2个细节😱"


def test_title_from_style_colon_pattern():
    # 英文「**风格**: *标题*」模式（LinkedIn 3 版本），跳过正文章节小标题
    art = ("### Adapted LinkedIn-Friendly Titles (3 Versions, Aligned to LinkedIn's Professional Audience)\n"
           "1. **Suspense-Driven**: *Shenzhen China-Russia Summit: Will It Break the Dollar's Grip?*\n"
           "2. **Opinion-Led**: *Shenzhen Summit Signals a New Local Currency Shift*\n"
           "---\n"
           "### Full LinkedIn Article\n"
           "#### Hotspot Introduction (Original: 热点引入)\n"
           "##### Positive Evidence Chain (USD Bypass Driving Change)\n"
           "- Post-2014 sanctions…")
    raw = json.dumps({"lkdn_output": [art]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["articles"][0]["topic"] == "Shenzhen China-Russia Summit: Will It Break the Dollar's Grip?"


def test_prompt_element_kept():
    # 文生图提示词元素照常保留（用户确认后续加工用），不误判为拒答
    art = "### 适配小红书竖版的文生图提示词\n小红书竖版图文插画，画面主体：深圳平安金融中心…"
    raw = json.dumps({"xhs_output": [art]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and len(r["articles"]) == 1 and r["skipped"] == 0
    # 标题取提示词正文首行即可；重点是元素被保留（用户后续流程加工用）
    assert r["articles"][0]["topic"]


def test_xhs_title_from_opening_hook_after_struct_note():
    # 小红书无【标题】标注：结构说明标题后的第一行 hook 才是大标题
    art1 = ("### 一、改写后小红书风格商业投资文案\n"
            "家人们！最近中俄美会晤释放的信号，直接戳中全球投资的底层逻辑！\n"
            "🔹核心逻辑：利益共生的平等合作是关键风向标！")
    art2 = ("---\n"
            "### 【改写后小红书风格财经文案】\n"
            "🤔地缘不再是玄学！中俄美这波操作，直接戳中投资者钱包核心！\n"
            "最近中俄美表态要搞伙伴关系…\n"
            "#宏观配置 #地缘与投资")
    raw = json.dumps({"xhs_output": [art1, art2]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["articles"][0]["topic"] == "家人们！最近中俄美会晤释放的信号，直接戳中全球投资的底层逻辑！"
    assert r["articles"][1]["topic"] == "🤔地缘不再是玄学！中俄美这波操作，直接戳中投资者钱包核心！"


def test_gzh_title_after_struct_note_no_regression():
    # 回归守护：结构说明后跟 markdown 标题时，应取该标题而非正文
    art = ("---\n"
           "### 微信公众号长文\n"
           "# 中俄美确立伙伴关系：全球投资的「和平红利」正在释放？\n"
           "各位关注全球投资的小伙伴们…")
    raw = json.dumps({"gzh_output": [art]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["articles"][0]["topic"] == "中俄美确立伙伴关系：全球投资的「和平红利」正在释放？"


# ---- 2026-11-20 重跑暴露的微博标题边界（BC-004）----


def test_wb_title_from_single_hashtag_after_fulltext_note():
    # 「### 微博版全文（...）」为结构说明，真标题是紧随的单话题标签 #...#
    art = ("### 微博版全文（带话题/短句/互动感）\n"
           "#入职时间才是职场隐形KPI？#\n"
           "---\n"
           "#### 【热点炸了】\n"
           "某AI初创上市首日，员工期权账户集体跳涨…")
    raw = json.dumps({"wb_output": [art]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and len(r["articles"]) == 1
    assert r["articles"][0]["topic"] == "入职时间才是职场隐形KPI？"


def test_wb_title_skips_trailing_hashtag_stack():
    # 微博元素以【真标题】开头、文末跟「#标签1 #标签2」标签堆，不得误取标签堆为标题
    art = ("【深圳水贝挤爆！黄金置换潮藏着啥信号🔥】\n"
           "（热点引入）\n"
           "谁懂啊！工作日黄金市场人满为患😱\n"
           "…\n"
           "#深圳水贝 #黄金消费 #投资风向 #财经观察")
    raw = json.dumps({"wb_output": [art]}, ensure_ascii=False)
    r = parse_output(raw)
    assert r["ok"] and len(r["articles"]) == 1
    assert r["articles"][0]["topic"] == "【深圳水贝挤爆！黄金置换潮藏着啥信号🔥】"
