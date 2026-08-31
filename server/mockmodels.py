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

MODEL_POOL = [
    {
        "model_id": "swift-4b",
        "display_name": "迅答 Swift-4B",
        "provider": "mocklab",
        "endpoint": "https://api.mocklab.local/swift",
        "credential_ref": "vault://cred/swift-4b",
        "price_input": 0.10, "price_output": 0.20,
        "latency_ms_base": 320,
        "capabilities": {"tool_call": True, "vision": False, "streaming": True, "context_window": 32768, "thinking": False},
        "profile": {"chat": 0.96, "price": 0.42, "sourcing": 0.45, "capacity": 0.48, "port": 0.44,
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
        "capabilities": {"tool_call": True, "vision": True, "streaming": True, "context_window": 131072, "thinking": False},
        "profile": {"chat": 0.88, "price": 0.78, "sourcing": 0.80, "capacity": 0.79, "port": 0.76,
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
        "capabilities": {"tool_call": True, "vision": False, "streaming": True, "context_window": 65536, "thinking": True},
        "profile": {"chat": 0.60, "price": 0.95, "sourcing": 0.86, "capacity": 0.82, "port": 0.72,
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
        "capabilities": {"tool_call": True, "vision": False, "streaming": True, "context_window": 32768, "thinking": False},
        "profile": {"chat": 0.70, "price": 0.60, "sourcing": 0.58, "capacity": 0.90, "port": 0.94,
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
        "capabilities": {"tool_call": True, "vision": False, "streaming": True, "context_window": 131072, "thinking": False},
        "profile": {"chat": 0.72, "price": 0.58, "sourcing": 0.83, "capacity": 0.60, "port": 0.62,
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
        "capabilities": {"tool_call": True, "vision": True, "streaming": True, "context_window": 262144, "thinking": True},
        "profile": {"chat": 0.90, "price": 0.90, "sourcing": 0.90, "capacity": 0.88, "port": 0.87,
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
