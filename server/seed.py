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
            "INSERT OR IGNORE INTO models (model_id, display_name, provider, endpoint, credential_ref, "
            "price_input, price_output, capabilities, status, bank_coverage, latency_ms_base, profile, "
            "deploy_type, gpu_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (m["model_id"], m["display_name"], m["provider"], m["endpoint"], m["credential_ref"],
             m["price_input"], m["price_output"], db.j(m["capabilities"]), "active", 1.0,
             m["latency_ms_base"], db.j(m["profile"]),
             m.get("deploy_type", "api"), m.get("gpu_count", 0)))
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
            "INSERT OR IGNORE INTO policies (policy_id, name, scope, tenant_id, scene, params, latency_tier, "
            "allow_aggregation, explore_ratio, model_whitelist, budget_cap, enabled, ab_group, ab_split, version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (r[0], r[1], r[2], r[3], r[4], db.j(r[5]), r[6], r[7], r[8], db.j(r[9]), db.j(r[10]), r[11], r[12], r[13]))
        conn.execute(
            "INSERT OR IGNORE INTO policy_history (policy_id, version, snapshot, ts) VALUES (?,1,?,?)",
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


# v5.0 通用数据集：按能力维度组织的客观题（带标准答案），冷启动直接跑 benchmark 打分。
# coding 维度标注「公开榜单引用」——分数直接引公开代码榜单，不必自己出题跑分。
DIM_QUESTIONS = {
    "qa": [
        ("什么是保税仓，和普通仓库的核心区别是什么", "保税仓内货物暂缓缴纳关税，核心区别是海关监管与税务递延。"),
        ("电子提单相比纸质提单的主要优势有哪些", "流转快、防伪造、可在线背书转让，降低单证遗失风险。"),
        ("什么是供应链金融中的应收账款融资", "以应收账款为质押或转让标的向金融机构融资。"),
        ("多式联运和海铁联运是什么关系", "海铁联运是多式联运的一种具体形式。"),
        ("什么是滞期费，由谁承担", "超过约定装卸期产生的费用，通常由租船人承担。"),
        ("汇率避险常用的三种工具是什么", "远期结售汇、外汇期权、货币互换。"),
        ("EXW 和 FOB 贸易术语的责任划分区别", "EXW 卖方工厂交货责任最小；FOB 卖方负责装船越过船舷前的费用与风险。"),
        ("什么是安全库存，怎么确定", "为应对需求与补货波动而保留的缓冲库存，按服务水平和需求标准差确定。"),
        ("集装箱 TEU 是什么单位", "标准二十英尺集装箱换算单位。"),
        ("什么是甩柜，一般什么原因导致", "订舱后货物未被装船；多因舱位超订或港口拥堵。"),
        ("信用证付款方式的主要风险点是什么", "单证不符导致拒付；软条款风险。"),
        ("什么是碳关税（CBAM），对出口有什么影响", "欧盟对进口高碳产品征收的碳边境调节费用，抬高高碳出口成本。"),
    ],
    "coding": [
        ("写一个 SQL：按月统计订单表 orders 近半年每月的订单量", "SELECT strftime('%Y-%m', created_at) m, COUNT(*) FROM orders GROUP BY m。"),
        ("写一个 Python 函数，去掉列表中的重复元素并保持顺序", "用 dict.fromkeys(lst) 或 seen 集合遍历。"),
        ("这段代码报错 KeyError 怎么排查", "打印键集合，用 .get() 或 in 判断后再取值。"),
        ("写一个正则，匹配 11 位手机号", "^1[3-9]\\d{9}$"),
        ("写一个函数计算两个日期相差的天数", "用 datetime 相减取 .days。"),
        ("SQL 查询每个客户金额最高的一笔订单", "窗口函数 ROW_NUMBER() OVER (PARTITION BY customer ORDER BY amount DESC) 取第 1 行。"),
        ("如何给接口加一个简单的限流", "令牌桶或固定窗口计数器，超限返回 429。"),
        ("写一个脚本批量重命名目录下的 csv 文件加日期前缀", "os.listdir 过滤 .csv 后 os.rename 拼接日期前缀。"),
    ],
    "math": [
        ("集装箱利用率从 62% 提升到 71%，提升了多少个百分点", "9 个百分点。"),
        ("按年利率 4.2% 计算 500 万元贷款一年的利息是多少", "21 万元。"),
        ("一批货 1200 箱，每车装 85 箱，至少需要多少车", "15 车。"),
        ("运费上涨 15% 后又下降 10%，相对最初变化了多少", "上涨 3.5%。"),
        ("两个仓库分别有 340 和 260 件库存，要均衡到相等需要调拨多少件", "调拨 40 件。"),
        ("月环比增长 5%，连续 3 个月后累计增长约多少", "约 15.8%。"),
        ("某航线准班率 82%，一个月 50 个航次预计几次延误", "9 次。"),
        ("汇率从 7.25 变为 7.10，10 万美元货款少收多少人民币", "1.5 万元。"),
        ("按 3:2 的比例把 600 吨货分给两条船，各装多少", "360 吨和 240 吨。"),
        ("一个订单毛利率 18%，售价 25 万元，成本是多少", "20.5 万元。"),
    ],
    "writing": [
        ("起草一份因台风停止装卸作业的客户通知", "含停工时间、影响范围、恢复预估、联系人四要素。"),
        ("把「货到晚了别着急我们在催」改写成正式客服话术", "致歉+跟进措施+反馈渠道；原因与预计时间题干未给出，应写明确认后同步，不得编造。"),
        ("写一段仓库安全月活动的动员文案", "主题、目标、行动号召三段式。"),
        ("起草一份供应商年度评审会议纪要模板", "含议题、结论、待办、责任人、期限字段。"),
        ("写一封催收逾期账款的商务邮件", "语气克制：事实+账期+付款方式+后续动作。"),
        ("把这段口语化描述整理成周报条目：这周把华东的仓都盘完了问题不大", "华东区仓库盘点已完成、无重大问题；差异数据题干未给出，应写明待补充，不得编造指标。"),
        ("为新上线的运单查询功能写一段产品公告", "功能说明+入口+适用范围+反馈渠道。"),
        ("起草一份节前发货截止时间的对外通知", "截止时间、恢复时间、应急联系人。"),
    ],
    "chat": [
        ("你好", "问候并简要说明能做什么。"),
        ("你是谁", "自我介绍：本平台的智能助手。"),
        ("谢谢你的帮助", "礼貌回应。"),
        ("早上好", "问候回应。"),
        ("你能做什么", "列举可协助的事项。"),
        ("在吗", "确认在线并询问需求。"),
    ],
    "multimodal": [
        ("识别这张图片里的集装箱箱号", "读取箱体 11 位编码（4 位字母+7 位数字）。"),
        ("这张截图里的系统报错是什么意思", "识别报错文本并解释原因。"),
        ("从这张磅单照片里提取毛重和皮重", "OCR 提取毛重/皮重/净重字段。"),
        ("看这张舱位图，还能装几个 40 尺柜", "按图中空位统计 40 尺柜余位。"),
        ("识别这段语音里客户反馈的问题点", "转写并归纳客户诉求。"),
        ("这张发票扫描件的开票金额是多少", "OCR 提取价税合计金额。"),
        ("对比这两张仓库照片，找出堆放差异", "识别两图中货物堆放位置与数量差异。"),
        ("从这张签收单照片判断是否有破损备注", "识别手写备注区是否标注破损。"),
    ],
}


def seed_public_bank():
    """通用数据集冷启动底座：能力维度 × 客观题（标准答案判分，无需 LLM 裁判）。"""
    conn = db.get_conn()
    if conn.execute("SELECT COUNT(*) AS c FROM bank_queries WHERE tenant_id IS NULL").fetchone()["c"] > 0:
        return 0
    rng = random.Random(7)
    n = 0
    for dim, items in DIM_QUESTIONS.items():
        for text, ideal in items:
            qid = f"pub-{n:04d}"
            created = time.time() - rng.random() * 60 * 86400
            conn.execute(
                "INSERT INTO bank_queries (query_id, tenant_id, embedding, text_ref, query_text, domain_tags, "
                "created_at, ttl_days, source, ideal) VALUES (?,NULL,?,?,?,?,?,?,?,?)",
                (qid, db.j(embeddings.embed(text)), f"vault://public/{qid}", text, db.j([dim]),
                 created, 365, "public", ideal))
            src = "ground_truth"
            for m in mockmodels.MODEL_POOL:
                correct = mockmodels.is_correct(m["model_id"], m["profile"], text, dim)
                content, _ = mockmodels.gen_structured(text, dim, correct)
                conn.execute(
                    "INSERT INTO bank_responses (query_id, model_id, response_embedding, completion_tokens, "
                    "label_value, label_confidence, label_source, label_kind, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (qid, m["model_id"], db.j(embeddings.embed(content)), max(30, int(len(content) * 1.5)),
                     1.0 if correct else 0.0, 1.0, src, "capability", created, created))
            n += 1
    conn.commit()
    return n


# 数据飞轮种子：模拟已回流的终端用户 AB 采纳偏好（真实来源是客户端调 /v1/feedback）
# dimension 字段承载主题 key，与 Query 池聚类同口径
AB_QUERY_POOL = {
    "logistics": ["我的货三天没更新了帮我看看", "帮我催一下上海那票货", "破损赔付怎么申请", "取件改到明天上午可以吗"],
    "market": ["铁矿石行情这周怎么看", "欧线运价还会涨吗", "大豆价格指数帮我分析下", "现在订舱价格合适吗"],
    "compliance": ["这份合同的违约条款帮我看看", "锂电池出口要什么资质", "关税新政对我们有影响吗", "清关单证缺一项怎么补"],
    "analytics": ["帮我算下这单的毛利率", "本月华东线路成本汇总一下", "利润同比下滑的原因怎么分析", "这批订单数据做个透视"],
    "writing": ["帮我写个延误道歉通知", "把这段话改成正式邮件", "写一份月度经营总结开头", "起草个涨价函"],
    "tech": ["帮我看看这段 SQL 为什么慢", "写个脚本把运单号批量查一遍", "接口偶发超时怎么加重试", "数据同步失败怎么排查"],
}


def seed_ab_feedback(force=False):
    conn = db.get_conn()
    if not force and conn.execute("SELECT COUNT(*) AS c FROM ab_feedback").fetchone()["c"] > 0:
        return 0
    rng = random.Random(55)
    models = [m for m in mockmodels.MODEL_POOL]
    n = 0
    for day in range(7, 0, -1):
        for _ in range(rng.randint(7, 12)):
            dim = rng.choice(list(AB_QUERY_POOL.keys()))
            q = rng.choice(AB_QUERY_POOL[dim])
            pkey = mockmodels.QUERY_THEMES.get(dim, {}).get("profile_key", "general")
            pool = [m for m in models if (m["profile"].get(pkey, 0) > 0.05)]
            if len(pool) < 2:
                continue
            a, b = rng.sample(pool, 2)
            pa, pb = a["profile"].get(pkey, 0.5), b["profile"].get(pkey, 0.5)
            winner, loser = (a, b) if rng.random() < pa / max(0.01, pa + pb) else (b, a)
            ts = time.time() - day * 86400 + rng.random() * 86400 * 0.7
            content, _ = mockmodels.gen_structured(q, dim, True)
            conn.execute(
                "INSERT INTO ab_feedback (fb_id, trace_id, query_text, dimension, winner, losers, source, ts, "
                "chosen_content) VALUES (?,?,?,?,?,?,?,?,?)",
                (db.new_id(), None, q, dim, winner["model_id"], db.j([loser["model_id"]]), "api", ts, content[:500]))
            n += 1
    conn.commit()
    return n


def migrate_flywheel_v5():
    """v5.0 一次性迁移：场景数据集 → 通用数据集（维度客观题），下线 LLM 裁判，启用数据飞轮。"""
    conn = db.get_conn()
    if conn.execute("SELECT v FROM kv_settings WHERE k='v5_flywheel'").fetchone():
        return False
    conn.execute("DELETE FROM bank_responses")
    conn.execute("DELETE FROM bank_queries")
    for k in ("scene_rounds", "custom_scenes", "policy_profile_gen", "judge_model", "judge_model_info"):
        conn.execute("DELETE FROM kv_settings WHERE k=?", (k,))
    for m in mockmodels.MODEL_POOL:
        conn.execute("UPDATE models SET deploy_type=?, gpu_count=? WHERE model_id=?",
                     (m.get("deploy_type", "api"), m.get("gpu_count", 0), m["model_id"]))
    conn.execute("INSERT OR REPLACE INTO kv_settings (k, v) VALUES ('ab_sampling_rate', '0.2')")
    conn.execute("INSERT OR REPLACE INTO kv_settings (k, v) VALUES ('v5_flywheel', '1')")
    conn.commit()
    db.audit("system", "migrate_flywheel_v5", {"note": "通用数据集冷启动 + 数据飞轮启用，LLM 裁判下线"})
    return True


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


def migrate_trust_v5_1():
    """v5.1 可信度迁移（用户质疑 Q02/Q06）：修正会奖励编造的标准答案；榜单引用补来源与快照日期。"""
    conn = db.get_conn()
    if conn.execute("SELECT v FROM kv_settings WHERE k='v5_1_trust'").fetchone():
        return False
    fixes = {
        "把这段口语化描述整理成周报条目：这周把华东的仓都盘完了问题不大":
            "华东区仓库盘点已完成、无重大问题；差异数据题干未给出，应写明待补充，不得编造指标。",
        "把「货到晚了别着急我们在催」改写成正式客服话术":
            "致歉+跟进措施+反馈渠道；原因与预计时间题干未给出，应写明确认后同步，不得编造。",
    }
    for q, ideal in fixes.items():
        conn.execute("UPDATE bank_queries SET ideal=? WHERE query_text=?", (ideal, q))
    conn.execute("INSERT OR REPLACE INTO kv_settings (k, v) VALUES ('v5_1_trust', '1')")
    conn.commit()
    db.audit("system", "migrate_trust_v5_1", {"ideal_fixed": len(fixes)})
    return True



# v6.0 Query 池：冷启动不再预置 QA 对，随机探索期收集真实 query（种子模拟已收集 520+ 条）。
# 主题是聚类的隐藏真值（演示用），映射到模型隐藏画像的既有键取答题概率。


def seed_query_pool():
    """随机探索期收集的 query 池（种子模拟 520+ 条，隐藏主题作聚类真值）。"""
    conn = db.get_conn()
    if conn.execute("SELECT COUNT(*) AS c FROM bank_queries WHERE source='collected'").fetchone()["c"] > 0:
        return 0
    rng = random.Random(66)
    n = 0
    for theme, cfg in mockmodels.QUERY_THEMES.items():
        per = rng.randint(78, 96)
        for i in range(per):
            t = cfg["templates"][i % len(cfg["templates"])]
            text = t.format(c=rng.choice(COMMODITIES), p=rng.choice(PORTS))
            if i >= len(cfg["templates"]):
                text = text + ["", "，急", "，尽快", "，谢谢", "，这周内"][i % 5]
            qid = f"col-{theme[:3]}{i:03d}"
            created = time.time() - rng.random() * 21 * 86400
            served = rng.choice(mockmodels.MODEL_POOL)["model_id"]
            conn.execute(
                "INSERT OR IGNORE INTO bank_queries (query_id, tenant_id, embedding, text_ref, query_text, "
                "domain_tags, created_at, ttl_days, source) VALUES (?,?,?,?,?,?,?,?,?)",
                (qid, TENANT, db.j(embeddings.embed(text)), f"vault://collected/{qid}", text,
                 db.j([theme]), created, 365, "collected"))
            n += 1
    conn.commit()
    return n


def migrate_pool_v6():
    """v6.0：数据集从 QA 对转为 Query 池 + 聚类簇快照；预置版本清空，回到随机探索初始态。"""
    conn = db.get_conn()
    if conn.execute("SELECT v FROM kv_settings WHERE k='v6_query_pool'").fetchone():
        return False
    conn.execute("DELETE FROM bank_responses")
    conn.execute("DELETE FROM bank_queries")
    conn.execute("DELETE FROM dataset_versions")
    conn.execute("DELETE FROM ab_feedback")  # 旧维度口径的 AB 种子重灌为主题口径
    for k in ("policy_profile_gen", "custom_scenes", "scene_rounds", "dimension_meta"):
        conn.execute("DELETE FROM kv_settings WHERE k=?", (k,))
    for r in conn.execute("SELECT k FROM kv_settings WHERE k LIKE 'profile_evolution_v%' "
                          "OR k LIKE 'profile_matrix_v%' OR k LIKE 'dataset_clusters_v%'").fetchall():
        conn.execute("DELETE FROM kv_settings WHERE k=?", (r["k"],))
    conn.execute("INSERT OR REPLACE INTO kv_settings (k, v) VALUES ('v6_query_pool', '1')")
    conn.commit()
    db.audit("system", "migrate_pool_v6", {"note": "QA 对下线，转 Query 池 + 聚类定版 + 版本级画像"})
    return True


def migrate_generic_v5_3():
    """v5.3 口径修正：冷启动就是一套通用数据集，不区分内置 benchmark / 公开榜单引用。"""
    conn = db.get_conn()
    if conn.execute("SELECT v FROM kv_settings WHERE k='v5_3_generic'").fetchone():
        return False
    conn.execute("INSERT OR REPLACE INTO kv_settings (k, v) VALUES ('dimension_meta', '{}')")
    conn.execute("UPDATE bank_responses SET label_source='ground_truth' WHERE label_source='leaderboard'")
    conn.execute("INSERT OR REPLACE INTO kv_settings (k, v) VALUES ('v5_3_generic', '1')")
    conn.commit()
    db.audit("system", "migrate_generic_v5_3", {"note": "冷启动统一为通用数据集，榜单引用概念下线"})
    return True


def migrate_dataset_v5_2():
    """v5.2 数据集版本化：v1 = 冷启动 QA 对；回流经接口显式导入生成新版本，可回滚。"""
    conn = db.get_conn()
    if conn.execute("SELECT v FROM kv_settings WHERE k='v5_2_dataset'").fetchone():
        return False
    conn.execute("UPDATE bank_queries SET dataset_version=1 WHERE tenant_id IS NULL AND dataset_version IS NULL")
    cold = conn.execute("SELECT COUNT(*) AS c FROM bank_queries WHERE tenant_id IS NULL").fetchone()["c"]
    if not conn.execute("SELECT 1 FROM dataset_versions WHERE version=1").fetchone():
        conn.execute("INSERT INTO dataset_versions (version, ts, note, cold_count, reflow_count, active) "
                     "VALUES (1,?,?,?,0,1)", (db.now_ts(), "冷启动：内置 benchmark QA 对", cold))
    # 历史回流补采纳内容（QA 对的答案侧）
    for r in conn.execute("SELECT fb_id, query_text, dimension FROM ab_feedback WHERE chosen_content IS NULL").fetchall():
        content, _ = mockmodels.gen_structured(r["query_text"] or "", r["dimension"] or "qa", True)
        conn.execute("UPDATE ab_feedback SET chosen_content=? WHERE fb_id=?", (content[:500], r["fb_id"]))
    conn.execute("DELETE FROM kv_settings WHERE k IN ('profile_evolution','evolve_last_ts')")
    conn.execute("INSERT OR REPLACE INTO kv_settings (k, v) VALUES ('v5_2_dataset', '1')")
    conn.commit()
    db.audit("system", "migrate_dataset_v5_2", {"cold_count": cold})
    return True


def migrate_bench_v7():
    """v7 智能路由方案：画像 = benchmark 表 + 成本。清空 v6 的问题池 / 版本 / 采纳 / Judge 数据。"""
    conn = db.get_conn()
    if conn.execute("SELECT v FROM kv_settings WHERE k='v7_bench'").fetchone():
        return False
    conn.execute("DELETE FROM bank_responses")
    conn.execute("DELETE FROM bank_queries WHERE source IN ('collected','reflow','reflow_staged')")
    conn.execute("DELETE FROM dataset_versions")
    conn.execute("DELETE FROM ab_feedback")
    for r in conn.execute("SELECT k FROM kv_settings WHERE k LIKE 'dataset_clusters_v%' OR k LIKE 'profile_matrix_v%' "
                          "OR k IN ('judge_model_info','ab_sampling_rate','policy_profile_gen','v6_query_pool')").fetchall():
        conn.execute("DELETE FROM kv_settings WHERE k=?", (r["k"],))
    conn.execute("INSERT OR REPLACE INTO kv_settings (k, v) VALUES ('v7_bench', '1')")
    conn.commit()
    db.audit("system", "migrate_bench_v7", {"note": "画像改为 benchmark 分数表 + 成本；数据集/飞轮/Judge 下线"})
    return True


def migrate_model_name_v71():
    """v7.1：模型「型号」与「接入实例」解耦——model_name 存官方型号（可重复），model_id 变纯实例键。
    同一型号可分别接官方 API 与自有部署多条。存量行型号回填为 model_id。"""
    conn = db.get_conn()
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(models)").fetchall()]
    if "model_name" in cols:
        return False
    conn.execute("ALTER TABLE models ADD COLUMN model_name TEXT")
    conn.execute("UPDATE models SET model_name=model_id WHERE model_name IS NULL")
    conn.commit()
    return True


V2_SEED_CARDS = [
    {"name": "通用选择", "component_type": "select.single", "semantic_category": "collect",
     "description": "需要用户在若干候选中做单项选择时使用：处理方式、方案、时间段等。候选项按当前对话动态给出，也可由管理员预置。",
     "field_bindings": {"config": {"options": []}},
     "text_templates": {"prompt": "请选择", "submit": "确认"}},
    {"name": "多项勾选", "component_type": "select.multi", "semantic_category": "collect",
     "description": "需要用户勾选多个候选项时使用：偏好调查、批量操作确认等。候选项按当前对话动态给出。",
     "field_bindings": {"config": {"options": []}},
     "text_templates": {"prompt": "请勾选适用项", "submit": "提交"}},
    {"name": "信息登记", "component_type": "form.structured", "semantic_category": "collect",
     "description": "需要用户补充结构化信息时使用。字段在平台定义（属性定死），模型可预填已知值。",
     "field_bindings": {"config": {"fields": [
         {"key": "contact_name", "label": "联系人", "type": "text", "required": True},
         {"key": "contact_phone", "label": "联系方式", "type": "text", "required": True}]}},
     "text_templates": {"prompt": "请补充以下信息", "submit": "提交"}},
    {"name": "高风险确认", "component_type": "control.confirm", "semantic_category": "control",
     "description": "执行不可逆或高风险动作前使用：取消订单、变更关键信息、发起赔付等，用户明确确认后才继续。",
     "field_bindings": {"config": {}},
     "text_templates": {"prompt": "请确认是否继续", "submit": "确认执行"}},
    {"name": "回答评价", "component_type": "feedback.binary", "semantic_category": "evaluate",
     "description": "对一条回答收集赞 / 踩评价，可分维度；用于回答质量的持续观测。",
     "field_bindings": {"config": {"dimensions": [
         {"key": "accuracy", "label": "答得准确吗"}, {"key": "helpful", "label": "对你有帮助吗"}]}},
     "text_templates": {"prompt": "这条回答怎么样"}},
    {"name": "方案择优", "component_type": "feedback.preference", "semantic_category": "evaluate",
     "description": "多个候选回答或方案让用户择优（多模型对比、多方案对比场景），采纳结果回传。",
     "field_bindings": {"config": {}},
     "text_templates": {"prompt": "你更认可哪一份"}},
    {"name": "数据表格", "component_type": "table", "semantic_category": "present",
     "description": "要表达清单、对比、多行记录等结构化数据时使用，替代大段文字。展示类，无提交。",
     "field_bindings": {"config": {}}, "text_templates": {}},
    {"name": "趋势图", "component_type": "chart.line", "semantic_category": "present",
     "description": "要表达数列随时间的走势时使用（折线图）。展示类，无提交。",
     "field_bindings": {"config": {}}, "text_templates": {}},
    {"name": "对比图", "component_type": "chart.bar", "semantic_category": "present",
     "description": "要表达类别之间的数量对比或分布时使用（柱状图）。展示类，无提交。",
     "field_bindings": {"config": {}}, "text_templates": {}},
    {"name": "占比图", "component_type": "chart.pie", "semantic_category": "present",
     "description": "要表达部分与整体的占比构成时使用。展示类，无提交。",
     "field_bindings": {"config": {}}, "text_templates": {}},
    {"name": "指标卡", "component_type": "metric.card", "semantic_category": "present",
     "description": "要突出一个关键数字（含涨跌与基线）时使用。展示类，无提交。",
     "field_bindings": {"config": {}}, "text_templates": {}},
    {"name": "时间线", "component_type": "timeline", "semantic_category": "present",
     "description": "要表达事件先后过程、里程碑或进度时使用。展示类，无提交。",
     "field_bindings": {"config": {}}, "text_templates": {}},
    {"name": "步骤条", "component_type": "steps", "semantic_category": "present",
     "description": "要给出分步操作指引并标注当前步骤时使用。展示类，无提交。",
     "field_bindings": {"config": {}}, "text_templates": {}},
]


def migrate_assistant_v21():
    """v2.1：展示类扩容（趋势/对比/占比/指标/时间线/步骤）——补种新增的展示实例并挂到种子产品。"""
    conn = db.get_conn()
    if conn.execute("SELECT v FROM kv_settings WHERE k='v21_display'").fetchone():
        return False
    new_ids = []
    for payload in V2_SEED_CARDS:
        exists = conn.execute("SELECT card_id, status FROM cards WHERE tenant_id=? AND name=?",
                              (TENANT, payload["name"])).fetchone()
        if exists:
            if exists["status"] != "published":
                cards.transition(exists["card_id"], "publish", actor="seed")
            new_ids.append(exists["card_id"])
            continue
        card, errors = cards.create_card(TENANT, dict(payload))
        if errors:
            raise RuntimeError(f"v2.1 seed card failed: {errors}")
        cards.transition(card["card_id"], "publish", actor="seed")
        new_ids.append(card["card_id"])
    prow = conn.execute("SELECT product_id FROM products LIMIT 1").fetchone()
    if prow:
        conn.execute("UPDATE products SET card_ids=? WHERE product_id=?", (db.j(new_ids), prow["product_id"]))
    conn.execute("INSERT OR REPLACE INTO kv_settings (k, v) VALUES ('v21_display', '1')")
    conn.commit()
    db.audit("system", "migrate_assistant_v21", {"note": "展示类扩容至 7 类", "total": len(new_ids)})
    return True


def migrate_assistant_v2():
    """v2 智能助手交互：触发条件下线、组件集收敛为注册集（交互 5 + 展示 2）。
    旧业务组件卡（地图 / 下单 / 物流轨迹等）整体下线保留数据；种入泛化组件实例并挂到种子产品。"""
    conn = db.get_conn()
    if conn.execute("SELECT v FROM kv_settings WHERE k='v2_assistant'").fetchone():
        return False
    seed_names = {c["name"] for c in V2_SEED_CARDS}
    demoted = 0
    for r in conn.execute("SELECT card_id, name, status FROM cards").fetchall():
        if r["status"] == "published" and r["name"] not in seed_names:
            # v2 清场：旧业务场景组件（含类型合法但内容业务耦合的）一律下线，数据保留
            conn.execute("UPDATE cards SET status='offline' WHERE card_id=?", (r["card_id"],))
            demoted += 1
    new_ids = []
    for payload in V2_SEED_CARDS:
        exists = conn.execute("SELECT card_id FROM cards WHERE tenant_id=? AND name=?",
                              (TENANT, payload["name"])).fetchone()
        if exists:
            row = conn.execute("SELECT status FROM cards WHERE card_id=?", (exists["card_id"],)).fetchone()
            if row and row["status"] != "published":
                cards.transition(exists["card_id"], "publish", actor="seed")
            new_ids.append(exists["card_id"])
            continue
        card, errors = cards.create_card(TENANT, dict(payload))
        if errors:
            raise RuntimeError(f"v2 seed card failed: {errors}")
        card, err = cards.transition(card["card_id"], "publish", actor="seed")
        if err:
            raise RuntimeError(f"v2 seed publish failed: {err}")
        new_ids.append(card["card_id"])
    prow = conn.execute("SELECT product_id FROM products LIMIT 1").fetchone()
    if prow:
        conn.execute("UPDATE products SET card_ids=? WHERE product_id=?",
                     (db.j(new_ids), prow["product_id"]))
    conn.execute("INSERT OR REPLACE INTO kv_settings (k, v) VALUES ('v2_assistant', '1')")
    conn.commit()
    db.audit("system", "migrate_assistant_v2",
             {"note": "触发条件下线，组件集收敛为注册集（交互 5 + 展示 2）", "demoted": demoted, "seeded": len(new_ids)})
    return True


def migrate_products_v23():
    """产品表加出包记录列：pulled_hash / pulled_at（拉注册表时写入，检测线上包是否落后）。"""
    conn = db.get_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()}
    if "pulled_hash" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN pulled_hash TEXT")
        conn.execute("ALTER TABLE products ADD COLUMN pulled_at REAL")
        conn.commit()
        db.audit("system", "migrate_products_v23", {"note": "出包记录列"})


def migrate_products_v22():
    """v2.2：多产品种子——不同产品定位（供应链 / 生产力 / 财务），演示按产品切换组件集与品牌。"""
    conn = db.get_conn()
    if conn.execute("SELECT v FROM kv_settings WHERE k='v22_products'").fetchone():
        return False
    pub = [r["card_id"] for r in conn.execute(
        "SELECT card_id FROM cards WHERE status='published' ORDER BY created_at").fetchall()]
    interact = [r["card_id"] for r in conn.execute(
        "SELECT card_id FROM cards WHERE status='published' AND semantic_category IN ('collect','control','evaluate')").fetchall()]
    present = [r["card_id"] for r in conn.execute(
        "SELECT card_id FROM cards WHERE status='published' AND semantic_category='present'").fetchall()]
    brands = ["brand-tokens.default.json", "brand-tokens.chainbao.json",
              "brand-tokens.meetnote.json", "brand-tokens.caishui.json"]
    have = {r["name"] for r in conn.execute("SELECT name FROM products").fetchall()}
    rows = [
        ("链运宝 App", brands[1], pub),                                    # 供应链物流工具：全组件 · 青瓷
        ("智会纪要", brands[2], present + interact[:2]),                    # 生产力工具：偏展示 · 黛紫
        ("财税小助", brands[3], interact[:4] + present[:3]),                # 财务 SaaS：表单/确认 · 松绿
    ]
    n = 0
    for name, bf, ids in rows:
        if name in have:
            continue
        conn.execute("INSERT INTO products (product_id, name, brand_file, card_ids, created_at) VALUES (?,?,?,?,?)",
                     ("prod-" + db.new_id()[:8], name, bf, db.j(ids), db.now_ts()))
        n += 1
    conn.execute("INSERT OR REPLACE INTO kv_settings (k, v) VALUES ('v22_products', '1')")
    conn.commit()
    db.audit("system", "migrate_products_v22", {"seeded": n})
    return True


def run_all():
    db.init_db()
    seed_models()
    seed_policies()
    seed_products()
    seed_cards()
    migrate_flywheel_v5()
    migrate_trust_v5_1()
    migrate_dataset_v5_2()
    migrate_generic_v5_3()
    migrate_pool_v6()
    migrate_bench_v7()
    migrate_model_name_v71()
    migrate_assistant_v2()
    migrate_assistant_v21()
    migrate_products_v22()
    migrate_products_v23()
    n_bank = 0
    n_fb = seed_ab_feedback()
    n_hist = seed_history()
    migrate_questionnaire()
    return {"bank_queries": n_bank, "ab_feedback": n_fb, "history_traces": n_hist}


if __name__ == "__main__":
    print(run_all())
