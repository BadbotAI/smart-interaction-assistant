"""种子数据：模型池、策略、示例卡片、公共 bank 冷启动、7 天模拟运行历史。

公共 bank 相当于 LLMRouterBench 公开数据构建的底座层（tenant_id 为 NULL，只读）。
模拟历史让看板与 Trace 首次打开即有可分析数据。
"""
import random
import time

from . import cards, db, embeddings, mockmodels

TENANT = "tenant-demo"

QUERY_TEMPLATES = {
    "price": ["{c}近期价格走势如何", "分析一下{c}的运价波动", "{c}价格指数未来会涨还是跌", "帮我看下{c}行情"],
    "sourcing": ["帮我找{c}的供应商", "对{c}做一轮采购寻源比价", "有哪些{c}货源可选", "{c}询价应该找谁"],
    "capacity": ["{c}线路的运力调度方案", "安排{c}的运输舱位", "{c}干线配载怎么优化", "给我{c}的船期方案"],
    "port": ["{p}的靠泊计划怎么样", "{p}装卸作业进度", "{p}堆场周转情况分析", "{p}清关要多久"],
    "compliance": ["{c}出口合规审查要点", "{c}是否涉及制裁名单", "{c}危险品申报要求", "{c}的合同条款风险"],
    "weather": ["台风对{p}航线的影响", "{p}未来一周气象风险", "{p}会封航吗", "大雾对{p}作业的影响"],
    "analytics": ["{c}业务线经营分析", "{c}板块利润同比情况", "汇总{c}季度报表要点", "{c}成本趋势分析"],
    "chat": ["你好", "你是谁", "介绍一下你自己", "谢谢你的帮助", "早上好", "你能做什么"],
    "service": ["我的货延误了怎么办", "帮我改一下送货时间", "可以约明天上门取件吗", "货物破损了要求赔付",
                "帮我查一下这票货到哪了", "发票信息开错了怎么改", "上门取件想换个地址", "回单什么时候能给我"],
}
COMMODITIES = ["铁矿石", "铜精矿", "原油", "液化天然气", "大豆", "煤炭", "铝锭", "纸浆"]
PORTS = ["上海港", "宁波舟山港", "新加坡港", "鹿特丹港", "洛杉矶港", "汉堡港"]


def gen_queries(per_domain=24):
    rng = random.Random(42)
    out = []
    for domain, templates in QUERY_TEMPLATES.items():
        for i in range(per_domain):
            t = templates[i % len(templates)]
            text = t.format(c=rng.choice(COMMODITIES), p=rng.choice(PORTS))
            out.append((domain, f"{text}"))
    return out


def seed_models():
    conn = db.get_conn()
    for m in mockmodels.MODEL_POOL:
        conn.execute(
            "INSERT OR REPLACE INTO models (model_id, display_name, provider, endpoint, credential_ref, "
            "price_input, price_output, capabilities, status, bank_coverage, latency_ms_base, profile) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (m["model_id"], m["display_name"], m["provider"], m["endpoint"], m["credential_ref"],
             m["price_input"], m["price_output"], db.j(m["capabilities"]), "active", 1.0,
             m["latency_ms_base"], db.j(m["profile"])))
    # 默认兜底模型：路由故障时的最终切换目标（能力均衡、成本适中的通用模型）
    if not conn.execute("SELECT 1 FROM models WHERE is_default=1").fetchone():
        conn.execute("UPDATE models SET is_default=1 WHERE model_id='atlas-72b'")
    conn.commit()


def seed_products():
    conn = db.get_conn()
    if conn.execute("SELECT 1 FROM products LIMIT 1").fetchone():
        return
    cards = conn.execute("SELECT card_id FROM cards WHERE status='published' LIMIT 3").fetchall()
    conn.execute("INSERT INTO products (product_id, name, brand_file, card_ids, created_at) VALUES (?,?,?,?,?)",
                 ("prod-seed0001", "官网智能客服", "brand-tokens.harbor.json",
                  db.j([r["card_id"] for r in cards]), db.now_ts()))
    conn.commit()


def seed_policies():
    conn = db.get_conn()
    paper = dict(K=3, N_base=50, beta=0.5, gamma=0.95, eps=0.5, sigma=0.3, delta=0.2, t=0.8, max_agg_tokens=13000)
    rows = [
        ("policy-global-balanced", "全局均衡", "global", None, None, paper, "balanced", 1, 0.05, [], {}, 1, None, 50),
        ("policy-scene-fast", "省钱优先", "custom", TENANT, None, {**paper, "K": 1, "alpha": 0.25}, "fast", 0, 0.02, [], {}, 1, None, 50),
        ("policy-scene-quality", "质量优先", "custom", TENANT, None, {**paper, "t": 0.7, "alpha": 0.9}, "quality", 1, 0.08, [], {}, 1, None, 50),
    ]
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO policies (policy_id, name, scope, tenant_id, scene, params, latency_tier, "
            "allow_aggregation, explore_ratio, model_whitelist, budget_cap, enabled, ab_group, ab_split, version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (r[0], r[1], r[2], r[3], r[4], db.j(r[5]), r[6], r[7], r[8], db.j(r[9]), db.j(r[10]), r[11], r[12], r[13]))
        conn.execute(
            "INSERT OR REPLACE INTO policy_history (policy_id, version, snapshot, ts) VALUES (?,1,?,?)",
            (r[0], db.j({"params": r[5], "latency_tier": r[6], "explore_ratio": r[8]}), db.now_ts()))
    conn.commit()


SEED_CARDS = [
    {
        "name": "运输方案选择",
        "description": "当用户需要在多个运输方案间做决定时使用",
        "component_type": "select.card",
        "trigger_description": "用户需要在多个运输或物流方案中选择一个时调用。适用于海运、铁路、公路等运输方式的取舍决策。",
        "trigger_examples": ["帮我选一个运输方案", "海运和铁路哪个好", "这批货走哪条线路"],
        "option_source": {"type": "model_generated", "hint_values": ["海运直达", "海铁联运", "中欧班列"]},
        "text_templates": {"prompt": "请选择本次运输方案", "submit": "确认方案"},
        "emit_fields": ["user_selection", "modified_from_default"],
        "emit_targets": ["model", "dashboard", "label_store"],
        "label_polarity_map": {"rule": "selection_accept", "confidence": 0.6, "label_kind": "preference"},
        "echo_results": True,
    },
    {
        "name": "供应商比选",
        "description": "多供应商多维度权衡后选择，供应链最高频决策形态",
        "component_type": "matrix.compare+select",
        "trigger_description": "用户需要对多个供应商或多个方案做多维度权衡比较并最终选定一个时调用。维度包括价格、时效、风险、合规。",
        "trigger_examples": ["对比一下这几家供应商", "哪家供应商更靠谱", "供应商比选"],
        "option_source": {"type": "api", "endpoint": "/mock/suppliers"},
        "text_templates": {"prompt": "综合权衡后请选定供应商", "submit": "确认选择"},
        "emit_fields": ["user_selection", "matrix_snapshot"],
        "emit_targets": ["model", "dashboard", "label_store"],
        "label_polarity_map": {"rule": "selection_accept", "confidence": 0.7, "label_kind": "capability"},
        "echo_results": True,
    },
    {
        "name": "补充装运信息",
        "description": "信息不足时向用户采集结构化字段",
        "component_type": "form.structured",
        "trigger_description": "回答运输、订舱、报关问题缺少关键信息（货物品类、重量、起运港、目的港、期望时效）时调用，向用户采集结构化字段。",
        "trigger_examples": ["帮我订舱", "安排一票货", "我要发一批货"],
        "field_bindings": {"fields": [
            {"key": "cargo", "label": "货物品类", "type": "text", "required": True},
            {"key": "weight", "label": "重量（吨）", "type": "number", "required": True},
            {"key": "origin", "label": "起运港", "type": "text", "required": True},
            {"key": "dest", "label": "目的港", "type": "text", "required": True},
        ]},
        "text_templates": {"prompt": "请补充以下装运信息", "submit": "提交"},
        "emit_fields": ["form_values"],
        "emit_targets": ["model", "dashboard"],
    },
    {
        "name": "物流异常处理",
        "description": "客服场景：用户反馈货物延误、丢失、破损等异常",
        "component_type": "select.card",
        "trigger_description": "当用户反馈货物延误、丢失、破损、物流长时间无更新等异常时触发本场景。适用于该场景下的咨询、求助与处理请求；不适用于普通闲聊或与此无关的问题。",
        "trigger_examples": ["我的货延误了怎么办", "货物到现在还没更新物流", "包裹破损了要怎么处理"],
        "option_source": {"type": "static", "values": []},
        "field_bindings": {"config": {"options": ["加急催办", "改约送达时间", "申请赔付", "转人工客服"],
                                       "recommended_default": "加急催办"}},
        "text_templates": {"prompt": "请选择处理方式", "submit": "确认",
                            "reply": "很抱歉给你带来不便，我们已记录这条物流异常。你可以选择以下处理方式，提交后会立即为你跟进。"},
        "emit_fields": ["user_selection"],
        "emit_targets": ["model", "dashboard", "label_store"],
        "label_polarity_map": {"rule": "selection_accept", "confidence": 0.6, "label_kind": "preference"},
        "echo_results": True,
    },
    {
        "name": "高风险操作确认",
        "description": "写操作、外发、付费调用前的确认闸门",
        "component_type": "control.confirm",
        "trigger_description": "即将执行不可逆或有成本的动作（下单、支付、外发邮件、删除数据、调用付费接口）前调用，请求用户确认。",
        "trigger_examples": ["帮我下单", "把报告发给客户", "删除这条记录"],
        "text_templates": {"title": "操作确认", "confirm": "确认执行", "cancel": "取消"},
        "emit_fields": ["action", "decision"],
        "emit_targets": ["dashboard"],
    },
]


SEED_CARDS += [
    {
        "name": "预约上门取件",
        "description": "客服场景：用户约上门取件的时间段",
        "component_type": "picker.timerange",
        "trigger_description": "用户想预约或修改上门取件 / 送货时间时调用，采集期望的起止时间段。",
        "trigger_examples": ["可以约明天上门取件吗", "帮我改一下取件时间", "什么时候能来收货"],
        "field_bindings": {"config": {"display": "time"}},
        "text_templates": {"prompt": "请选择方便的取件时间段", "submit": "确认预约",
                            "reply": "好的，取件师傅会在你选择的时间段内上门。"},
        "emit_fields": ["user_selection"],
        "emit_targets": ["model", "dashboard"],
    },
    {
        "name": "收货地址确认",
        "description": "客服场景：确认或修改收货地址",
        "component_type": "picker.location",
        "trigger_description": "用户需要确认、指定或修改收货 / 取件地址时调用，支持常用节点与地址搜索。",
        "trigger_examples": ["这批货送到哪个仓", "帮我改一下收货地址", "换个地方取件"],
        "option_source": {"type": "static", "values": ["上海仓", "宁波舟山港", "青岛港"]},
        "field_bindings": {"config": {"options": ["上海仓", "宁波舟山港", "青岛港"],
                                       "option_ids": ["opt-sh", "opt-nb", "opt-qd"],
                                       "placeholder": "搜索地址，或从常用地点选择"}},
        "text_templates": {"prompt": "请确认收货地点", "submit": "确认地址"},
        "emit_fields": ["user_selection"],
        "emit_targets": ["model", "dashboard"],
    },
    {
        "name": "破损凭证上传",
        "description": "客服场景：破损赔付需要用户上传照片凭证",
        "component_type": "upload.image",
        "trigger_description": "用户反馈货物破损、包装受损并申请赔付时调用，采集破损部位照片作为核赔凭证。",
        "trigger_examples": ["货物破损了要求赔付", "外箱压坏了", "我拍了破损照片给你"],
        "field_bindings": {"config": {"placeholder": "请拍摄或上传破损部位的清晰照片"}},
        "text_templates": {"prompt": "上传破损照片", "submit": "提交凭证",
                            "reply": "请上传破损部位的照片，我们据此核定赔付，一般 1 个工作日内出结果。"},
        "emit_fields": ["user_selection"],
        "emit_targets": ["model", "dashboard"],
    },
    {
        "name": "查件后引导",
        "description": "查询物流后的下一步入口：追问 / 催办 / 官网运单页",
        "component_type": "entry.link",
        "trigger_description": "用户查询某票货物的物流进度并得到回答后调用，给出可点的追问与服务入口，引导下一步动作。",
        "trigger_examples": ["帮我查一下这票货到哪了", "这票货物流到哪一步了", "查下运单进度"],
        "field_bindings": {"config": {
            "options": ["为什么会延误", "预计什么时候能到", "帮我催一下这票货", "打开官网运单页"],
            "option_ids": ["opt-why", "opt-eta", "opt-urge", "opt-site"],
            "option_actions": {
                "帮我催一下这票货": {"prompt": "用户希望加急催办这票货，请生成催办工单号并告知预计反馈时间（2 小时内）"},
                "打开官网运单页": {"api": "https://www.example-scm.com/waybill"}
            }}},
        "text_templates": {"prompt": "你可能还想问", "submit": "提交",
                            "reply": "这票货已到宁波舟山港中转，预计后天送达。"},
        "emit_fields": ["user_selection"],
        "emit_targets": ["model", "dashboard"],
    },
    {
        "name": "增值服务下单",
        "description": "加固 / 保价等增值服务直接下单",
        "component_type": "commerce.order",
        "trigger_description": "用户提出货物需要加固、保价、包装耗材等增值服务并有购买意向时调用，展示服务商品并直接下单。",
        "trigger_examples": ["这批设备怕震帮我加固", "帮我买个运输保价", "要个木箱包装"],
        "field_bindings": {"config": {
            "options": ["木箱加固", "运输保价", "防潮包装"],
            "option_ids": ["opt-crate", "opt-ins", "opt-damp"],
            "option_meta": {
                "木箱加固": {"desc": "出口级木箱，防潮防震，适合精密设备", "price": 120, "price_original": 150, "sales": 2300, "tags": ["防潮", "承重加强"]},
                "运输保价": {"desc": "按货值 0.3% 投保，破损丢失全额赔付", "price": 50, "price_original": 60, "sales": 5100, "tags": ["全额赔付"]},
                "防潮包装": {"desc": "真空 + 干燥剂双层防潮，海运首选", "price": 35, "sales": 860, "tags": ["海运推荐"]}}}},
        "text_templates": {"prompt": "选择增值服务", "submit": "下单",
                            "reply": "根据这批货的情况，推荐以下增值服务，可直接下单："},
        "emit_fields": ["user_selection"],
        "emit_targets": ["model", "dashboard"],
    },
    {
        "name": "优惠专区入口",
        "description": "活动 / 专区的通栏跳转入口",
        "component_type": "entry.link",
        "trigger_description": "用户询问优惠、活动、专区或想了解更多服务时调用，给出可点击的专区入口。",
        "trigger_examples": ["还有什么优惠活动吗", "有没有折扣专区", "更多服务在哪看"],
        "field_bindings": {"config": {
            "options": ["神券团购专区", "增值服务商城"],
            "option_ids": ["opt-coupon", "opt-vas"],
            "option_meta": {"神券团购专区": {"desc": "咖啡茶饮 5 折起，限时领券"},
                             "增值服务商城": {"desc": "加固 · 保价 · 包装耗材"}},
            "option_actions": {"神券团购专区": {"api": "https://www.example-scm.com/coupon"},
                                "增值服务商城": {"api": "https://www.example-scm.com/vas"}}}},
        "text_templates": {"prompt": "", "submit": "提交",
                            "reply": "为你找到两个专区，点击直达："},
        "emit_fields": ["user_selection"],
        "emit_targets": ["model", "dashboard"],
    },
    {
        "name": "旺季运力调查",
        "description": "旺季前收集客户的舱位与运力需求（活动已结束，示例下线态）",
        "component_type": "scale.likert",
        "trigger_description": "旺季（十一、双十一前）询问客户对舱位保障的紧迫程度时调用，收集运力需求强度。",
        "trigger_examples": ["旺季舱位紧张吗", "双十一前运力够不够"],
        "field_bindings": {"config": {"likert": {"left": "完全不急", "right": "非常紧急", "from": 1, "to": 5}}},
        "text_templates": {"prompt": "这批货的舱位需求有多紧急？", "submit": "提交"},
        "emit_fields": ["user_selection"],
        "emit_targets": ["model", "dashboard"],
        "_seed_status": "offline",
    },
    {
        "name": "售后回访登记",
        "description": "结案后回访信息登记（编写中，示例草稿态）",
        "component_type": "form.structured",
        "trigger_description": "工单结案后向客户收集回访信息（联系时间、改进建议）时调用。",
        "trigger_examples": ["帮我登记一下回访", "结案后怎么反馈"],
        "field_bindings": {"config": {"fields": [
            {"key": "callback_time", "label": "方便回访的时间", "type": "text", "required": True},
            {"key": "suggestion", "label": "改进建议", "type": "text", "required": False}]}},
        "text_templates": {"prompt": "请留下回访信息", "submit": "提交"},
        "emit_fields": ["form_values"],
        "emit_targets": ["dashboard"],
        "_seed_status": "draft",
    },
]


def seed_cards():
    conn = db.get_conn()
    for payload in SEED_CARDS:
        exists = conn.execute("SELECT card_id FROM cards WHERE tenant_id=? AND name=?",
                              (TENANT, payload["name"])).fetchone()
        if exists:
            continue
        target = payload.pop("_seed_status", "published")
        card, errors = cards.create_card(TENANT, payload)
        if errors:
            raise RuntimeError(f"seed card failed: {errors}")
        if target == "draft":
            continue  # 草稿态：不发布
        card, err = cards.transition(card["card_id"], "publish", actor="seed")
        if err:
            raise RuntimeError(f"seed publish failed: {err}")
        if target == "offline":
            cards.transition(card["card_id"], "offline", actor="seed")
            continue
        conn.execute("INSERT OR REPLACE INTO card_refs (agent_id, card_id, version) VALUES (?,?,?)",
                     ("agent-logistics-assistant", card["card_id"], card["version"]))
    conn.commit()


def seed_public_bank():
    conn = db.get_conn()
    if conn.execute("SELECT COUNT(*) AS c FROM bank_queries WHERE tenant_id IS NULL").fetchone()["c"] > 0:
        return 0
    rng = random.Random(7)
    n = 0
    for domain, text in gen_queries():
        qid = f"pub-{n:04d}"
        created = time.time() - rng.random() * 120 * 86400
        conn.execute(
            "INSERT INTO bank_queries (query_id, tenant_id, embedding, text_ref, query_text, domain_tags, "
            "created_at, ttl_days, source) VALUES (?,NULL,?,?,?,?,?,?,?)",
            (qid, db.j(embeddings.embed(text)), f"vault://public/{qid}", text, db.j([domain]),
             created, 365, "public"))
        for m in mockmodels.MODEL_POOL:
            correct = mockmodels.is_correct(m["model_id"], m["profile"], text, domain)
            content, _ = mockmodels.gen_structured(text, domain, correct)
            conn.execute(
                "INSERT INTO bank_responses (query_id, model_id, response_embedding, completion_tokens, "
                "label_value, label_confidence, label_source, label_kind, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (qid, m["model_id"], db.j(embeddings.embed(content)), max(30, int(len(content) * 1.5)),
                 1.0 if correct else 0.0, 1.0, "ground_truth", "capability", created, created))
        n += 1
    conn.commit()
    return n


def seed_history(days=7, per_day=22):
    """模拟过去 N 天的运行历史：traces、决策、事件、标签，让看板首开有数据。"""
    conn = db.get_conn()
    if conn.execute("SELECT COUNT(*) AS c FROM traces").fetchone()["c"] > 0:
        return 0
    rng = random.Random(99)
    queries = gen_queries()
    models = {m["model_id"]: m for m in mockmodels.MODEL_POOL}
    n = 0
    for day in range(days, 0, -1):
        for i in range(per_day):
            domain, qtext = rng.choice(queries)
            ts = time.time() - day * 86400 + rng.random() * 86400 * 0.6
            trace_id = db.new_id()
            turn_id = db.new_id()
            session_id = f"sess-hist-{day}-{i % 6}"
            user_id = f"u_hist{i % 9:02d}"
            is_explore = rng.random() < 0.05
            if domain == "chat":
                path = "fastlane"
            else:
                path = rng.choices(["fastlane", "routed", "aggregated"], weights=[0.35, 0.35, 0.30])[0]

            profile_rank = sorted(models.values(), key=lambda m: -m["profile"].get(domain, m["profile"]["general"]))
            if path == "fastlane":
                chosen = [profile_rank[0] if rng.random() < 0.7 else rng.choice(profile_rank[:3])]
            else:
                chosen = profile_rank[:3] if not is_explore else rng.sample(list(models.values()), 3)
            calls, contents = [], {}
            for m in chosen:
                correct = mockmodels.is_correct(m["model_id"], m["profile"], qtext, domain)
                content, _ = mockmodels.gen_structured(qtext, domain, correct)
                t_out = max(30, int(len(content) * 1.5))
                t_in = max(20, len(qtext) * 2)
                thinking = 500 if m["capabilities"].get("thinking") else 0
                calls.append({"model_id": m["model_id"], "latency_ms": int(m["latency_ms_base"] * (0.8 + rng.random() * 0.5)),
                              "tokens_in": t_in, "tokens_out": t_out, "tokens_thinking": thinking,
                              "cost": round(t_in / 1e6 * m["price_input"] + (t_out + thinking) / 1e6 * m["price_output"], 8),
                              "status": "ok", "resp_emb": None})
                contents[m["model_id"]] = (content, correct)
            final_model = chosen[0]["model_id"] if path != "aggregated" else profile_rank[0]["model_id"]
            switch = path
            total_cost = round(sum(c["cost"] for c in calls) * (1.3 if path == "aggregated" else 1.0), 8)
            latency = max(c["latency_ms"] for c in calls) + (1800 if path == "aggregated" else 0)
            conn.execute(
                "INSERT INTO traces (trace_id, tenant_id, session_id, turn_id, user_id, ts, status, switch_result, "
                "query_text, final_model, total_cost, total_latency_ms, is_explore, policy_id, ab_group) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (trace_id, TENANT, session_id, turn_id, user_id, ts, "ok", switch, qtext, final_model,
                 total_cost, latency, 1 if is_explore else 0, "policy-global-balanced", None))
            decision = {"trace_id": trace_id, "model_calls": calls, "switch_result": switch,
                        "final_model_or_aggregator": final_model, "is_explore": is_explore,
                        "candidate_models": [c["model_id"] for c in calls], "coarse_scores": {}, "fine_scores": {},
                        "total_cost": total_cost, "total_latency_ms": latency, "support_set_ids": []}
            conn.execute("INSERT INTO route_decisions (trace_id, tenant_id, policy_id, policy_version, decision) "
                         "VALUES (?,?,?,1,?)", (trace_id, TENANT, "policy-global-balanced", db.j(decision)))
            seq = 0
            for c in calls:
                seq += 1
                conn.execute("INSERT INTO spans (span_id, trace_id, span_type, ts, duration_ms, status, payload, seq) "
                             "VALUES (?,?,?,?,?,?,?,?)",
                             (db.new_id(), trace_id, "model_call", ts, c["latency_ms"], "ok",
                              db.j({k: c[k] for k in ("model_id", "latency_ms", "tokens_in", "tokens_out", "cost")}), seq))

            # 模拟组件事件：呈现渲染 + 部分交互 + 反馈
            if domain != "chat":
                comp = {"price": "chart.line", "analytics": "chart.bar", "capacity": "table",
                        "port": "timeline", "weather": "metric.card", "compliance": "citation.card",
                        "sourcing": "matrix.compare", "service": "track.map"}.get(domain, "table")
                _hist_event(conn, rng, trace_id, session_id, turn_id, user_id, ts + 1, "card_rendered",
                            comp, "present", "post_classification", {})
                if rng.random() < 0.75:
                    _hist_event(conn, rng, trace_id, session_id, turn_id, user_id, ts + 3, "card_interaction_started",
                                comp, "present", "post_classification", {"time_to_interact_ms": int(rng.random() * 5000 + 800)})
            if domain == "sourcing" and rng.random() < 0.7:
                sel = rng.choice(["供应商甲", "供应商乙", "供应商丙"])
                modified = rng.random() < 0.3
                _hist_event(conn, rng, trace_id, session_id, turn_id, user_id, ts + 8, "card_submitted",
                            "matrix.compare+select", "collect", "model_tool_call",
                            {"options_offered": ["供应商甲", "供应商乙", "供应商丙"], "recommended_default": "供应商甲",
                             "user_selection": sel, "modified_from_default": modified,
                             "time_to_submit_ms": int(rng.random() * 12000 + 3000)},
                            group={"enabled": True, "participants_count": 5, "aggregation_rule": "majority",
                                   "distribution": {"供应商甲": rng.randint(1, 4), "供应商乙": rng.randint(1, 3)},
                                   "final": sel, "abstained": rng.randint(0, 1)} if rng.random() < 0.4 else None)
            if rng.random() < 0.55:
                good = contents[final_model][1] if final_model in contents else rng.random() < 0.7
                dim = rng.choice(["capability", "preference"])
                lid = db.new_id()
                conn.execute(
                    "INSERT INTO labels (label_id, event_id, trace_id, tenant_id, model_id, label_kind, value, "
                    "confidence, source, status, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (lid, db.new_id(), trace_id, TENANT, final_model, dim,
                     1.0 if good else 0.0, 0.6, "explicit_binary", "admitted", None, ts + 15))
                _hist_event(conn, rng, trace_id, session_id, turn_id, user_id, ts + 15, "feedback_given",
                            "feedback.binary", "evaluate", "system_injected",
                            {"dimension": dim, "value": "up" if good else "down"})
            if path == "aggregated" and rng.random() < 0.35:
                win = profile_rank[0]["model_id"]
                losers = [c["model_id"] for c in calls if c["model_id"] != win]
                for mid, v in [(win, 1.0)] + [(l, 0.0) for l in losers]:
                    conn.execute(
                        "INSERT INTO labels (label_id, event_id, trace_id, tenant_id, model_id, label_kind, value, "
                        "confidence, source, status, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (db.new_id(), db.new_id(), trace_id, TENANT, mid, "capability", v, 0.9,
                         "explicit_preference", "admitted", None, ts + 20))
                _hist_event(conn, rng, trace_id, session_id, turn_id, user_id, ts + 20, "feedback_given",
                            "feedback.preference", "evaluate", "system_injected",
                            {"selected_model_id": win, "unselected_model_ids": losers})
            day_key = time.strftime("%Y-%m-%d", time.localtime(ts))
            conn.execute(
                "INSERT INTO quota_usage (tenant_id, day, tokens, cost, requests) VALUES (?,?,?,?,1) "
                "ON CONFLICT(tenant_id, day) DO UPDATE SET tokens=tokens+?, cost=cost+?, requests=requests+1",
                (TENANT, day_key, sum(c["tokens_in"] + c["tokens_out"] for c in calls), total_cost,
                 sum(c["tokens_in"] + c["tokens_out"] for c in calls), total_cost))
            n += 1
    conn.commit()
    return n


def _hist_event(conn, rng, trace_id, session_id, turn_id, user_id, ts, event_type, comp, cat, src, payload, group=None):
    conn.execute(
        """INSERT INTO events (event_id, trace_id, tenant_id, session_id, turn_id, user_id, ts, event_type,
           card, route_context, payload, group_info, label_hint, schema_version, admitted, reject_reason)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL,'1.0.0',1,NULL)""",
        (db.new_id(), trace_id, TENANT, session_id, turn_id, user_id, ts, event_type,
         db.j({"card_id": None, "card_version": None, "component_type": comp,
               "semantic_category": cat, "trigger_source": src}),
         db.j({}), db.j(payload), db.j(group)))


def migrate_questionnaire():
    """问卷模版化改造的存量迁移：
    1. 旧的复杂群体模式（group_mode）转为简单回显开关（echo_results）
    2. 为开启回显的种子问题补少量历史回答，让回显首开即有数据
    """
    conn = db.get_conn()
    conn.execute("UPDATE cards SET echo_results=1, group_mode=NULL "
                 "WHERE group_mode IS NOT NULL OR name IN ('供应商比选','运输方案选择')")
    rng = random.Random(31)
    seeds = {
        "供应商比选": ["华骏国际货代", "中远供应链", "环球捷运"],
        "运输方案选择": ["海运直达", "海铁联运", "中欧班列"],
        "物流异常处理": ["加急催办", "改约送达时间", "申请赔付", "转人工客服"],
    }
    for name, options in seeds.items():
        row = conn.execute("SELECT card_id, version, component_type FROM cards WHERE name=? AND status='published'",
                           (name,)).fetchone()
        if not row:
            continue
        existing = conn.execute(
            "SELECT COUNT(*) AS c FROM events WHERE event_type='card_submitted' "
            "AND json_extract(card,'$.card_id')=?", (row["card_id"],)).fetchone()["c"]
        if existing > 0:
            continue
        for i in range(7):
            weights = [len(options) - k for k in range(len(options))]
            sel = rng.choices(options, weights=weights)[0]
            ts = time.time() - rng.random() * 5 * 86400
            conn.execute(
                """INSERT INTO events (event_id, trace_id, tenant_id, session_id, turn_id, user_id, ts, event_type,
                   card, route_context, payload, group_info, label_hint, schema_version, admitted, reject_reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,'1.0.0',1,NULL)""",
                (db.new_id(), db.new_id(), TENANT, f"sess-echo-{i}", db.new_id(), f"u_echo{i:02d}", ts,
                 "card_submitted",
                 db.j({"card_id": row["card_id"], "card_version": row["version"],
                       "component_type": row["component_type"], "semantic_category": "collect",
                       "trigger_source": "model_tool_call"}),
                 db.j({}), db.j({"options_offered": options, "user_selection": sel,
                                 "recommended_default": options[0],
                                 "modified_from_default": sel != options[0]})))
    conn.commit()


def run_all():
    db.init_db()
    seed_models()
    seed_policies()
    seed_products()
    seed_cards()
    n_bank = seed_public_bank()
    n_hist = seed_history()
    migrate_questionnaire()
    return {"bank_queries": n_bank, "history_traces": n_hist}


if __name__ == "__main__":
    print(run_all())
