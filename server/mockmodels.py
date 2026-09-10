"""Mock 模型池。

每个模型有一份隐藏能力画像（profile：各领域答对概率）。回答正确与否由
hash(model_id, query) 播种的确定性伪随机决定——同一问题重复提问结果稳定，
便于验证 JiSi 路由是否学到了真实的能力分布。

生产环境替换为真实模型 API 时，仅 call_model() 需要改动。
"""
import asyncio
import hashlib
import random

from . import db

# 领域关键词表：呈现型后置判定与正确率模拟共用
DOMAIN_KEYWORDS = {
    "price": ["价格", "报价", "行情", "涨", "跌", "运价", "指数", "成本走势", "波动"],
    "sourcing": ["采购", "寻源", "供应商", "询价", "比价", "招标", "货源"],
    "capacity": ["运力", "调度", "车辆", "船期", "舱位", "运输方案", "配载", "干线"],
    "port": ["港口", "靠泊", "装卸", "堆场", "提单", "清关", "锚地", "泊位"],
    "compliance": ["合规", "审查", "制裁", "危险品", "条款", "合同", "禁运", "关税"],
    "weather": ["台风", "天气", "气象", "暴雨", "风浪", "封航", "大雾"],
    "analytics": ["分析", "报表", "经营", "利润", "毛利", "汇总", "同比", "环比", "趋势"],
    "chat": ["你好", "谢谢", "你是谁", "介绍一下你", "聊", "在吗", "早上好"],
}

# ---------- 能力维度（v5.0 通用数据集冷启动） ----------
# 通用数据集不按业务场景切，按模型能力维度切：维度与模型能力强相关，冷启动打分才有区分度。
DIMENSIONS = {
    "qa": "通用问答", "coding": "代码", "math": "数学推理", "writing": "长文写作",
    "multimodal": "多模态理解", "chat": "日常闲聊", "general": "通用",
}

DIM_KEYWORDS = {
    "coding": ["代码", "函数", "报错", "bug", "接口", "SQL", "脚本", "正则", "编程", "调试", "部署脚本"],
    "math": ["计算", "多少", "概率", "求解", "方程", "推导", "证明", "百分比", "利率", "增长率"],
    "writing": ["写一篇", "写一份", "起草", "润色", "文案", "总结报告", "公文", "演讲稿", "周报", "通知"],
    "multimodal": ["图片", "图像", "截图", "照片", "识别图", "看图", "音频", "语音", "视频", "扫描件"],
    "chat": ["你好", "谢谢", "你是谁", "介绍一下你", "聊", "在吗", "早上好"],
}


def classify_dimension(text: str) -> str:
    """三层路由的维度判定：先给硬规则层用（multimodal/chat），再给维度匹配层用。
    闲聊判定保守：问候词不能盖住真实任务——命中其他维度或文本较长时不算闲聊。"""
    scores = {}
    for dim, words in DIM_KEYWORDS.items():
        s = sum(1 for w in words if w in text)
        if s:
            scores[dim] = s
    if scores.get("multimodal"):
        return "multimodal"  # 多模态是硬规则：命中即定，不与其他维度比票
    others = {d: s for d, s in scores.items() if d != "chat"}
    if scores.get("chat") and not others and len(text) <= 12:
        return "chat"
    if not others:
        return "qa" if len(text) >= 6 else "general"
    return max(others, key=others.get)


MODEL_POOL = [
    {
        "model_id": "swift-4b",
        "display_name": "迅答 Swift-4B",
        "provider": "mocklab",
        "endpoint": "https://api.mocklab.local/swift",
        "credential_ref": "vault://cred/swift-4b",
        "price_input": 0.10, "price_output": 0.20,
        "latency_ms_base": 320,
        "deploy_type": "api", "gpu_count": 0,
        "capabilities": {"tool_call": True, "vision": False, "streaming": True, "context_window": 32768, "thinking": False},
        "profile": {"qa": 0.62, "coding": 0.35, "math": 0.30, "writing": 0.45, "multimodal": 0.0, "chat": 0.96, "price": 0.42, "sourcing": 0.45, "capacity": 0.48, "port": 0.44,
                    "compliance": 0.38, "weather": 0.62, "analytics": 0.40, "general": 0.55},
    },
    {
        "model_id": "atlas-72b",
        "display_name": "衡岳 Atlas-72B",
        "provider": "mocklab",
        "endpoint": "https://api.mocklab.local/atlas",
        "credential_ref": "vault://cred/atlas-72b",
        "price_input": 0.60, "price_output": 1.20,
        "latency_ms_base": 900,
        "deploy_type": "self_hosted", "gpu_count": 16,
        "capabilities": {"tool_call": True, "vision": True, "streaming": True, "context_window": 131072, "thinking": False},
        "profile": {"qa": 0.80, "coding": 0.68, "math": 0.62, "writing": 0.75, "multimodal": 0.78, "chat": 0.88, "price": 0.78, "sourcing": 0.80, "capacity": 0.79, "port": 0.76,
                    "compliance": 0.75, "weather": 0.80, "analytics": 0.78, "general": 0.80},
    },
    {
        "model_id": "sage-r1",
        "display_name": "沉思 Sage-R1",
        "provider": "deepmock",
        "endpoint": "https://api.deepmock.local/sage",
        "credential_ref": "vault://cred/sage-r1",
        "price_input": 1.00, "price_output": 2.50,
        "latency_ms_base": 2100,
        "deploy_type": "api", "gpu_count": 0,
        "capabilities": {"tool_call": True, "vision": False, "streaming": True, "context_window": 65536, "thinking": True},
        "profile": {"qa": 0.78, "coding": 0.85, "math": 0.94, "writing": 0.70, "multimodal": 0.0, "chat": 0.60, "price": 0.95, "sourcing": 0.86, "capacity": 0.82, "port": 0.72,
                    "compliance": 0.84, "weather": 0.70, "analytics": 0.95, "general": 0.82},
    },
    {
        "model_id": "harbor-13b",
        "display_name": "港航 Harbor-13B",
        "provider": "oceanmock",
        "endpoint": "https://api.oceanmock.local/harbor",
        "credential_ref": "vault://cred/harbor-13b",
        "price_input": 0.30, "price_output": 0.60,
        "latency_ms_base": 620,
        "deploy_type": "self_hosted", "gpu_count": 4,
        "capabilities": {"tool_call": True, "vision": False, "streaming": True, "context_window": 32768, "thinking": False},
        "profile": {"qa": 0.66, "coding": 0.55, "math": 0.48, "writing": 0.60, "multimodal": 0.0, "chat": 0.70, "price": 0.60, "sourcing": 0.58, "capacity": 0.90, "port": 0.94,
                    "compliance": 0.55, "weather": 0.82, "analytics": 0.56, "general": 0.62},
    },
    {
        "model_id": "lexi-34b",
        "display_name": "法准 Lexi-34B",
        "provider": "mocklab",
        "endpoint": "https://api.mocklab.local/lexi",
        "credential_ref": "vault://cred/lexi-34b",
        "price_input": 0.50, "price_output": 1.00,
        "latency_ms_base": 780,
        "deploy_type": "api", "gpu_count": 0,
        "capabilities": {"tool_call": True, "vision": False, "streaming": True, "context_window": 131072, "thinking": False},
        "profile": {"qa": 0.72, "coding": 0.50, "math": 0.52, "writing": 0.88, "multimodal": 0.0, "chat": 0.72, "price": 0.58, "sourcing": 0.83, "capacity": 0.60, "port": 0.62,
                    "compliance": 0.95, "weather": 0.58, "analytics": 0.66, "general": 0.68},
    },
    {
        "model_id": "nova-x",
        "display_name": "曜极 Nova-X",
        "provider": "starmock",
        "endpoint": "https://api.starmock.local/nova",
        "credential_ref": "vault://cred/nova-x",
        "price_input": 3.00, "price_output": 9.00,
        "latency_ms_base": 1500,
        "deploy_type": "api", "gpu_count": 0,
        "capabilities": {"tool_call": True, "vision": True, "streaming": True, "context_window": 262144, "thinking": True},
        "profile": {"qa": 0.90, "coding": 0.88, "math": 0.86, "writing": 0.90, "multimodal": 0.92, "chat": 0.90, "price": 0.90, "sourcing": 0.90, "capacity": 0.88, "port": 0.87,
                    "compliance": 0.90, "weather": 0.88, "analytics": 0.90, "general": 0.90},
    },
]

SIM_SPEED = 0.35  # 模拟延迟缩放系数，1.0 为真实量级


def classify_domain(text: str) -> str:
    scores = {}
    for domain, words in DOMAIN_KEYWORDS.items():
        s = sum(1 for w in words if w in text)
        if s:
            scores[domain] = s
    if not scores:
        return "general"
    return max(scores, key=scores.get)


def _rng(*parts) -> random.Random:
    seed = int.from_bytes(hashlib.md5("|".join(str(p) for p in parts).encode()).digest()[:8], "big")
    return random.Random(seed)


def is_correct(model_id: str, profile: dict, query: str, domain: str) -> bool:
    acc = profile.get(domain, profile.get("general", 0.6))
    return _rng("correct", model_id, query).random() < acc


# ---------- 回答内容生成（含结构化数据，供呈现型后置判定使用） ----------

def _series(rng, n=8, base=100, drift=0.0):
    vals, v = [], base
    for _ in range(n):
        v = max(1, v * (1 + drift + (rng.random() - 0.5) * 0.12))
        vals.append(round(v, 1))
    return vals


def gen_structured(query: str, domain: str, correct: bool):
    """按领域生成回答文本 + 结构化数据。数据由 query 播种，模型间可比。"""
    rng = _rng("data", query)
    wrong = "" if correct else "（结论方向存在偏差）"
    if domain == "price":
        weeks = [f"W{i+1}" for i in range(8)]
        vals = _series(rng, 8, 1800 + rng.random() * 600, 0.02)
        trend = "上行" if vals[-1] > vals[0] else "下行"
        if not correct:
            trend = "下行" if trend == "上行" else "上行"
        text = f"近八周价格整体呈{trend}趋势，最新值 {vals[-1]}，较期初变动 {round((vals[-1]/vals[0]-1)*100,1)}%。建议关注供需两端的边际变化。{wrong}"
        data = {"kind": "chart.line", "params": {"title": "价格走势（近8周）", "x_axis": weeks,
                "series": [{"name": "价格指数", "values": vals}]}}
        return text, data
    if domain == "analytics":
        cats = ["华东", "华南", "华北", "西南", "海外"]
        vals = [round(200 + rng.random() * 400) for _ in cats]
        top = cats[vals.index(max(vals))]
        text = f"分区域看，{top} 贡献最高（{max(vals)}），整体环比增速 {round(rng.random()*8+1,1)}%。{wrong}"
        data = {"kind": "chart.bar", "params": {"title": "分区域经营对比", "categories": cats,
                "series": [{"name": "营收（万元）", "values": vals}]}}
        return text, data
    if domain == "capacity":
        rows = []
        for name in ["方案A · 海运直达", "方案B · 海铁联运", "方案C · 中欧班列"]:
            rows.append([name, f"{round(18+rng.random()*20)}天", f"${round(1200+rng.random()*1800)}", f"{round(rng.random()*30+60)}%"])
        text = f"三条运输方案在时效与成本上各有取舍，运力富余度差异明显。{wrong}"
        data = {"kind": "table", "params": {"title": "运力方案一览", "columns": ["方案", "时效", "单箱成本", "舱位富余"], "rows": rows}}
        return text, data
    if domain == "port":
        events = [
            {"ts": "08:00", "title": "抵达锚地", "desc": "等待引航"},
            {"ts": "10:30", "title": "靠泊作业", "desc": f"预计装卸 {round(800+rng.random()*600)} TEU"},
            {"ts": "18:00", "title": "堆场转运", "desc": "重箱进场"},
            {"ts": "22:00", "title": "离泊", "desc": "预计准班"},
        ]
        text = f"当前靠泊计划整体可控，关键路径在装卸窗口。{wrong}"
        data = {"kind": "timeline", "params": {"title": "港口作业时间线", "events": events}}
        return text, data
    if domain == "weather":
        val = round(rng.random() * 12 + 28, 1)
        text = f"未来 72 小时受台风外围影响，沿海风力最高 {val} m/s，建议提前调整靠泊计划。{wrong}"
        data = {"kind": "metric.card", "params": {"label": "预计最大阵风", "value": val, "unit": "m/s",
                "delta": f"+{round(rng.random()*6+2,1)}", "baseline": "常年同期"}}
        return text, data
    if domain == "compliance":
        text = f"该批货物涉及两项需人工复核的合规要点：目的港管制清单与危险品申报一致性。{wrong}"
        data = {"kind": "citation.card", "params": {
            "claim": "目的港所在国对该 HS 编码存在附加许可要求",
            "sources": [
                {"title": "目的港海关公告 2026-014", "url": "https://example.local/customs/2026-014", "snippet": "对该类商品实施进口许可管理……", "confidence": 0.92},
                {"title": "国际制裁名单季度更新", "url": "https://example.local/sanctions/q2", "snippet": "本季度新增受限实体 37 家……", "confidence": 0.81},
            ]}}
        return text, data
    if domain == "sourcing":
        options = ["供应商甲", "供应商乙", "供应商丙"]
        dims = ["价格", "时效", "风险", "合规"]
        values = [[round(rng.random() * 4 + 5, 1) for _ in dims] for _ in options]
        best = options[max(range(3), key=lambda i: sum(values[i]))]
        if not correct:
            best = options[min(range(3), key=lambda i: sum(values[i]))]
        text = f"综合四个维度评估，{best} 的整体得分最高，建议进入询价环节。{wrong}"
        data = {"kind": "matrix.compare", "params": {"title": "供应商比选", "options": options,
                "dimensions": dims, "values": values, "recommended": best}}
        return text, data
    if domain == "chat":
        return "你好，我是本平台的智能助手，可以协助你做采购寻源、价格研判、运力调度、合规审查等分析。", None
    if domain == "service":
        if any(k in query for k in ("查", "到哪", "进度", "轨迹", "运单")):
            r = _rng("trk", query)
            spots = [("宁波舟山港中转场", "浙江省宁波市北仑区港区大道", 29.935, 121.844),
                     ("苏州分拨中心", "苏州市相城区望亭镇物流大道", 31.435, 120.520),
                     ("杭州转运枢纽", "杭州市萧山区空港物流园", 30.236, 120.434)]
            name, addr, lat, lon = spots[r.randrange(len(spots))]
            text = f"这票货正在干线运输，当前位于{name}，预计后天送达，轨迹如下。{wrong}"
            data = {"kind": "track.map", "params": {
                "title": "物流轨迹",
                "current": {"name": name, "addr": addr, "lat": lat, "lon": lon,
                             "status_text": "运输中", "updated_text": "2 小时前更新"},
                "nodes": [
                    {"time": "08-25 09:12", "text": "已揽收（上海仓）", "state": "done"},
                    {"time": "08-26 21:40", "text": "干线运输中，到达" + name, "state": "current"},
                    {"time": "", "text": "到达目的地网点，安排派送", "state": "todo"},
                    {"time": "", "text": "签收", "state": "todo"},
                ]}}
            return text, data
        acts = ["已为你登记并转交跟进", "已提交加急处理", "已为你预约变更", "已推送最新物流节点"]
        text = f"收到，你反馈的问题{acts[_rng('svc', query).randrange(len(acts))]}，预计 2 小时内有回复；也可以在下方直接选择处理方式。{wrong}"
        return text, None
    # general
    text = f"围绕这个问题，可以从现状、约束与可行动作三个层面展开：当前数据显示核心变量整体平稳，建议先明确目标区间再做取舍。{wrong}"
    return text, None


TONE = {
    "swift-4b": "简要结论：",
    "atlas-72b": "综合分析：",
    "sage-r1": "经过多步推理：",
    "harbor-13b": "结合港航实操经验：",
    "lexi-34b": "从合规与文本审查角度：",
    "nova-x": "深入评估后：",
}


async def call_model(model: dict, query: str, domain: str, timeout_s: float = 20.0) -> dict:
    """模拟一次模型调用。确定性：同一(model, query)返回相同内容与正确性。"""
    rng = _rng("call", model["model_id"], query)
    profile = model["profile"] if isinstance(model["profile"], dict) else db.dj(model["profile"], {})
    correct = is_correct(model["model_id"], profile, query, domain)
    text, data = gen_structured(query, domain, correct)
    content = TONE.get(model["model_id"], "") + text

    latency = model["latency_ms_base"] * (0.8 + rng.random() * 0.5) * SIM_SPEED
    caps = model["capabilities"] if isinstance(model["capabilities"], dict) else db.dj(model["capabilities"], {})
    thinking = int(rng.random() * 900 + 300) if caps.get("thinking") else 0
    if caps.get("thinking"):
        latency *= 1.4
    tokens_in = max(20, len(query) * 2)
    tokens_out = max(30, int(len(content) * 1.5))
    failed = rng.random() < 0.02  # 模拟偶发超时

    await asyncio.sleep(latency / 1000.0)
    if failed:
        return {"model_id": model["model_id"], "status": "timeout", "content": None, "data": None,
                "latency_ms": int(timeout_s * 1000), "tokens_in": tokens_in, "tokens_out": 0,
                "tokens_thinking": 0, "cost": 0.0, "correct": False}
    cost = tokens_in / 1e6 * model["price_input"] + (tokens_out + thinking) / 1e6 * model["price_output"]
    return {"model_id": model["model_id"], "status": "ok", "content": content, "data": data,
            "latency_ms": int(latency / SIM_SPEED), "tokens_in": tokens_in, "tokens_out": tokens_out,
            "tokens_thinking": thinking, "cost": round(cost, 8), "correct": correct}


def aggregate_answers(aggregator: dict, query: str, domain: str, answers: list) -> dict:
    """模拟聚合器重写：融合各候选回答，正确性取多数（模拟被带偏的可能性低）。"""
    rng = _rng("agg", aggregator["model_id"], query)
    correct_votes = sum(1 for a in answers if a.get("correct"))
    majority_correct = correct_votes * 2 >= len(answers)
    # 聚合器自身能力也影响结果
    profile = aggregator["profile"] if isinstance(aggregator["profile"], dict) else db.dj(aggregator["profile"], {})
    own = rng.random() < profile.get(domain, profile.get("general", 0.7))
    final_correct = majority_correct or own
    text, data = gen_structured(query, domain, final_correct)
    content = f"综合 {len(answers)} 个候选模型的回答并交叉验证：" + text
    tokens_in = sum(a["tokens_out"] for a in answers) + len(query) * 2
    tokens_out = max(40, int(len(content) * 1.5))
    cost = tokens_in / 1e6 * aggregator["price_input"] + tokens_out / 1e6 * aggregator["price_output"]
    latency = aggregator["latency_ms_base"] * 1.2
    return {"model_id": aggregator["model_id"], "status": "ok", "content": content, "data": data,
            "latency_ms": int(latency), "tokens_in": tokens_in, "tokens_out": tokens_out,
            "tokens_thinking": 0, "cost": round(cost, 8), "correct": final_correct}


# ---------- v6.0 Query 主题（聚类的隐藏真值 + 在线主题归类） ----------
QUERY_THEMES = {
    "logistics": {"label": "物流服务与异常", "profile_key": "service",
        "keywords": ["物流跟踪", "延误", "取件", "赔付"],
        "summary": "查件、延误、取件改约、破损赔付等售后服务类问题",
        "templates": ["我的货到哪了帮我查下{c}那票", "{c}这批货延误两天了怎么办", "帮我改约明天的取件时间",
                      "{c}的包裹破损了怎么申请赔付", "这票货物流三天没更新了", "帮我催一下{c}那单",
                      "取件地址想换到{p}怎么改", "回单什么时候能返回来"]},
    "market": {"label": "价格与行情", "profile_key": "price",
        "keywords": ["运价", "行情", "涨跌", "指数"],
        "summary": "运价与商品行情研判、价格指数走势类问题",
        "templates": ["{c}近期价格走势怎么看", "{p}航线运价这周涨了多少", "{c}的行情还会继续跌吗",
                      "帮我分析下{c}价格指数", "下个月{p}的运价怎么预判", "{c}现在入手合适吗"]},
    "compliance": {"label": "合同与合规", "profile_key": "compliance",
        "keywords": ["合同", "条款", "合规", "关税"],
        "summary": "合同条款审查、出口合规、关税申报类问题",
        "templates": ["这份{c}采购合同的付款条款帮我看看", "{c}出口到{p}要注意什么合规要求",
                      "危险品申报流程是怎样的", "{c}的关税怎么算", "合同里的违约条款这样写行不行",
                      "{p}的清关单证需要哪些"]},
    "analytics": {"label": "经营分析与报表", "profile_key": "analytics",
        "keywords": ["报表", "毛利", "同比", "汇总"],
        "summary": "经营数据分析、报表汇总、利润核算类问题",
        "templates": ["帮我算下{c}这单的毛利率", "汇总一下本月{p}线路的成本", "{c}业务线利润同比怎么样",
                      "这批订单的数据帮我做个分析", "月度经营报表的要点帮我列一下", "{c}板块环比数据怎么解读"]},
    "writing": {"label": "公文与写作", "profile_key": "writing",
        "keywords": ["通知", "邮件", "总结", "函件"],
        "summary": "通知、邮件、总结、函件等商务写作类需求",
        "templates": ["帮我写一份{p}停航的客户通知", "起草一封催收账款的邮件", "把这段话改成正式函件",
                      "写个季度工作总结的开头", "给{c}供应商写份涨价说明", "帮我润色这份会议纪要"]},
    "tech": {"label": "系统与技术", "profile_key": "coding",
        "keywords": ["接口", "SQL", "报错", "系统"],
        "summary": "系统对接、SQL 查询、接口报错等技术类问题",
        "templates": ["写个 SQL 统计{c}订单量", "接口偶发超时怎么加重试", "这段报错帮我看看什么原因",
                      "对接你们系统的 API 怎么调", "帮我写个批量导出的脚本", "数据同步失败一般怎么排查"]},
}


def classify_theme(text: str) -> str:
    """在线收集 query 的主题归类（演示实现；生产为 embedding 聚类的近邻指派）。"""
    best, score = "other", 0
    for theme, cfg in QUERY_THEMES.items():
        s = sum(1 for w in cfg["keywords"] if w in text)
        s += sum(1 for t in cfg["templates"] for w in [t[:4]] if w and w in text)
        if s > score:
            best, score = theme, s
    return best if score > 0 else "other"

# ---------- v7.0 Benchmark 维度画像（智能路由方案） ----------
# 画像 = 公开 benchmark 分数表 + 成本；智能路由模型判 query 相关维度，取平均分路由。
BENCH_DIMS = [
    {"key": "knowledge", "label": "通用知识", "bench": "MMLU", "desc": "常识与领域知识问答"},
    {"key": "math", "label": "数学推理", "bench": "GSM8K", "desc": "计算、推理与数量分析"},
    {"key": "coding", "label": "代码生成", "bench": "HumanEval", "desc": "代码编写与调试"},
    {"key": "writing", "label": "长文写作", "bench": "WritingBench", "desc": "公文、邮件、总结等商务写作"},
    {"key": "instruct", "label": "指令遵循", "bench": "IFEval", "desc": "格式、字数、步骤等约束的遵循"},
    {"key": "chinese", "label": "中文理解", "bench": "C-Eval", "desc": "中文语义、改写与翻译"},
    {"key": "multimodal", "label": "多模态理解", "bench": "MMMU", "desc": "图像、截图、扫描件理解"},
]

BENCH_DIM_KEYWORDS = {
    "math": ["计算", "利息", "毛利", "百分", "求解", "多少", "利率", "环比", "同比", "配载", "折算"],
    "coding": ["SQL", "sql", "代码", "脚本", "接口", "报错", "正则", "函数", "调试", "同步失败"],
    "writing": ["写一", "起草", "润色", "通知", "邮件", "总结", "函", "纪要", "汇报", "文案"],
    "chinese": ["翻译", "成语", "文言", "改写", "理解这段", "润色这段", "什么意思"],
    "instruct": ["按格式", "列表输出", "JSON", "表格输出", "分步骤", "字数", "按模板", "逐条"],
    "multimodal": ["图片", "图像", "截图", "照片", "识别图", "看图", "音频", "语音", "视频", "扫描件"],
    "knowledge": ["是什么", "为什么", "区别", "解释", "介绍", "怎么看", "要点", "要求", "流程"],
}


def classify_bench_dims(text: str):
    """智能路由模型判维的演示实现：返回 query 相关的 benchmark 维度（可多个，最多 2 个）。
    生产环境替换为真实小模型调用（结构化输出维度列表）。"""
    hits = []
    for dim, words in BENCH_DIM_KEYWORDS.items():
        s = sum(1 for w in words if w in text)
        if s:
            hits.append((s, dim))
    hits.sort(key=lambda x: -x[0])
    dims = [d for _, d in hits[:2]]
    if "multimodal" in [d for _, d in hits] and "multimodal" not in dims:
        dims = ["multimodal"] + dims[:1]
    if not dims:
        dims = ["knowledge"]
    return dims


# 榜单快照种子（0-100；None=该模型此维度无公开分，平均时跳过）。asof 为快照日期。
BENCH_SNAPSHOT = {
    "asof": "2026-08",
    "source": "公开榜单汇总（MMLU / GSM8K / HumanEval / WritingBench / IFEval / C-Eval / MMMU）",
    "scores": {
        "swift-4b":   {"knowledge": 58, "math": 31, "coding": 34, "writing": 47, "instruct": 55, "chinese": 62, "multimodal": None},
        "atlas-72b":  {"knowledge": 79, "math": 63, "coding": 67, "writing": 74, "instruct": 78, "chinese": 81, "multimodal": 74},
        "sage-r1":    {"knowledge": 80, "math": 93, "coding": 86, "writing": 69, "instruct": 74, "chinese": 72, "multimodal": None},
        "harbor-13b": {"knowledge": 64, "math": 49, "coding": 55, "writing": 60, "instruct": 66, "chinese": 70, "multimodal": None},
        "lexi-34b":   {"knowledge": 71, "math": 50, "coding": 52, "writing": 87, "instruct": 80, "chinese": 88, "multimodal": None},
        "nova-x":     {"knowledge": 90, "math": 87, "coding": 89, "writing": 90, "instruct": 88, "chinese": 85, "multimodal": 91},
    },
}



# ---------- v2 智能助手交互：动态选项模拟 ----------
# 演示「模型按当前对话动态给出候选项」：同一组件不同对话可能 3-6 项各不相同。
# 生产环境由大模型在组件调用参数 options 里直接给出。
_OPT_BANK = {
    "物流": ["加急派送", "改约取件时间", "转自提点", "申请破损赔付", "联系派送员", "查询最新轨迹"],
    "发票": ["电子普票", "增值税专票", "纸质普票", "先开电子后补纸质"],
    "价格": ["按最新报价执行", "锁定当前价格 7 天", "等待价格回落提醒", "人工议价"],
    "方案": ["方案 A · 时效优先", "方案 B · 成本优先", "方案 C · 均衡", "方案 D · 自定义组合"],
    "服务": ["转人工客服", "提交工单", "查看帮助文档", "稍后再说"],
}


def gen_options(query: str):
    text = query or ""
    for kw, opts in _OPT_BANK.items():
        if kw in text or any(w in text for w in {"物流": ["货", "快递", "派送", "延误"],
                                                  "发票": ["开票", "抬头", "报销"],
                                                  "价格": ["报价", "运价", "多少钱"],
                                                  "方案": ["选择", "对比", "哪个好"],
                                                  "服务": ["客服", "人工", "投诉"]}.get(kw, [])):
            n = 3 + (_stable_hash(text) % (len(opts) - 2)) if len(opts) > 3 else len(opts)
            return opts[:max(2, min(n, len(opts)))]
    base = ["确认继续", "查看详情", "换个方案", "稍后处理", "转人工跟进", "取消本次操作"]
    n = 3 + (_stable_hash(text or "q") % 4)
    return base[:n]


def _stable_hash(s: str) -> int:
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) % 100000
    return h


def gen_present_params(v2_type: str, query: str) -> dict:
    h0 = _stable_hash(query or "q")
    if v2_type == "pie":
        return {"title": "构成占比", "slices": [
            {"label": "华东", "value": 38 + h0 % 8}, {"label": "华南", "value": 24 + h0 % 6},
            {"label": "华北", "value": 21 + h0 % 5}, {"label": "其他", "value": 12}]}
    if v2_type == "metric":
        return {"label": "本月累计金额", "value": str(1200 + h0 % 300), "unit": "万元",
                "delta": f"{(h0 % 60) / 10:.1f}%", "baseline": "对比上月同期"}
    if v2_type == "timeline":
        return {"title": "处理进度", "events": [
            {"ts": "09:20", "title": "已受理", "desc": "工单创建"},
            {"ts": "10:05", "title": "处理中", "desc": "已分派专员跟进"},
            {"ts": "14:30", "title": "待确认", "desc": "方案已发出，等待确认"}]}
    if v2_type == "steps":
        return {"title": "操作指引", "steps": ["填写申请信息", "上传相关凭证", "等待审核", "查收处理结果"],
                "current_index": 1}
    """展示类组件的演示参数：模拟大模型按对话填入 table / chart 数据。
    生产环境由大模型在组件调用参数里直接给出。"""
    h = _stable_hash(query or "q")
    if v2_type == "table":
        rows = [["华东", str(320 + h % 80), f"{2.1 + (h % 10) / 10:.1f}%"],
                ["华南", str(280 + h % 60), f"{1.5 + (h % 8) / 10:.1f}%"],
                ["华北", str(350 + h % 50), f"{2.8 + (h % 6) / 10:.1f}%"],
                ["西南", str(190 + h % 40), f"{1.2 + (h % 5) / 10:.1f}%"]]
        return {"title": "分区域概览", "columns": ["区域", "数量", "环比"], "rows": rows}
    kind = "bar" if any(w in (query or "") for w in ("对比", "分布", "柱")) else "line"
    cats = ["4月", "5月", "6月", "7月", "8月", "9月"]
    vals = [round(100 + ((h + i * 37) % 90) + i * 6, 1) for i in range(6)]
    return {"title": "近半年走势", "kind": kind, "categories": cats,
            "series": [{"name": "金额（万元）", "values": vals}]}
