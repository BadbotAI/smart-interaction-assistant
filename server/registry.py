"""组件注册层（v2 智能助手交互）：把产品启用的组件实例编译成给 agent 的注册 JSON。

对大模型：组件即工具——只暴露「说明 + 参数 schema」，不暴露内部排布与样式。
agent 后端拉取本注册表声明给大模型；模型返回「组件 id + 参数」；前端 SDK 按产品的
组件定义与品牌风格渲染。业务逻辑全部留在 agent 侧，平台只管组件、样式与数据回收。
"""
from . import db

# ---------- V2 组件抽象：存储层 component_type → 注册类型 ----------
# 存储与渲染沿用既有 component_type（渲染引擎/信封协议不变）；注册层输出抽象类型。
V2_TYPE_MAP = {
    "select.single": "select", "select.multi": "select", "select.card": "select",
    "form.structured": "form",
    "control.confirm": "confirm",
    "feedback.binary": "feedback",
    "feedback.preference": "preference",
    "table": "table",
    "chart.line": "chart", "chart.bar": "chart",
}
V2_ALLOWED_CT = set(V2_TYPE_MAP)

# 注册类型元信息：交互类必有提交（唯一后端交互点）；展示类无提交
V2_META = {
    "select":     {"label": "选择表单", "interactive": True,
                   "desc": "单选 / 多选表单。选项可由管理员预置，也可留空由模型按当前对话动态给出（几项都行）。"},
    "form":       {"label": "信息表单", "interactive": True,
                   "desc": "属性定死的信息收集表单（字段由管理员在平台定义），模型可选传预填值。"},
    "confirm":    {"label": "操作确认", "interactive": True,
                   "desc": "高风险或关键动作的确认卡：用户明确点击确认 / 取消后才继续。"},
    "feedback":   {"label": "赞踩反馈", "interactive": True,
                   "desc": "对一条回答的赞 / 踩评价，可带维度。"},
    "preference": {"label": "偏好选择", "interactive": True,
                   "desc": "多个候选回答让用户择优（多模型 / 多方案对比场景）。"},
    "table":      {"label": "表格", "interactive": False,
                   "desc": "把模型要表达的结构化数据渲染成表格，替代大段文字。"},
    "chart":      {"label": "图表", "interactive": False,
                   "desc": "把数列渲染成折线图或柱状图，替代文字描述趋势。"},
}


def _cfg(card: dict) -> dict:
    return ((card.get("field_bindings") or {}).get("config") or {})


def params_schema_for(card: dict) -> dict:
    """模型调用该组件实例时要填的参数（JSON Schema 子集）。
    管理员已定死的内容不进 schema（对模型是黑盒），只在 fixed 里声明存在。"""
    ct = card.get("component_type") or ""
    v2 = V2_TYPE_MAP.get(ct)
    cfg = _cfg(card)
    schema = {"type": "object", "properties": {}, "required": []}
    props, req = schema["properties"], schema["required"]
    if v2 == "select":
        props["prompt"] = {"type": "string", "description": "向用户提出的问题"}
        req.append("prompt")
        fixed_opts = [o for o in (cfg.get("options") or []) if str(o).strip()]
        if not fixed_opts:
            props["options"] = {"type": "array", "items": {"type": "string"},
                                "minItems": 2, "maxItems": 8,
                                "description": "候选项文本，按当前对话动态给出"}
            req.append("options")
    elif v2 == "form":
        props["prefill"] = {"type": "object",
                            "description": "可选：按字段 key 预填已知值（字段定义见 fixed.fields）"}
    elif v2 == "confirm":
        props["prompt"] = {"type": "string", "description": "需要用户确认的动作描述"}
        props["summary"] = {"type": "string", "description": "可选：动作影响的一句话摘要"}
        req.append("prompt")
    elif v2 == "feedback":
        props["prompt"] = {"type": "string", "description": "可选：评价引导语"}
    elif v2 == "preference":
        props["candidates"] = {"type": "array", "minItems": 2, "maxItems": 4,
                               "items": {"type": "object",
                                         "properties": {"label": {"type": "string"},
                                                        "content": {"type": "string"}},
                                         "required": ["label", "content"]},
                               "description": "候选回答列表，用户从中择优"}
        req.append("candidates")
    elif v2 == "table":
        props["title"] = {"type": "string"}
        props["columns"] = {"type": "array", "items": {"type": "string"}, "minItems": 2}
        props["rows"] = {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}
        req.extend(["columns", "rows"])
    elif v2 == "chart":
        props["title"] = {"type": "string"}
        props["kind"] = {"type": "string", "enum": ["line", "bar"], "description": "折线或柱状"}
        props["categories"] = {"type": "array", "items": {"type": "string"}}
        props["series"] = {"type": "array",
                           "items": {"type": "object",
                                     "properties": {"name": {"type": "string"},
                                                    "values": {"type": "array", "items": {"type": "number"}}},
                                     "required": ["name", "values"]}}
        req.extend(["kind", "categories", "series"])
    return schema


def fixed_config_for(card: dict) -> dict:
    """管理员在平台定死的部分：模型不可改，仅供 agent 开发者了解组件行为。"""
    ct = card.get("component_type") or ""
    v2 = V2_TYPE_MAP.get(ct)
    cfg = _cfg(card)
    fixed = {}
    if v2 == "select":
        fixed["multi"] = ct == "select.multi"
        fixed["display"] = "card" if ct == "select.card" else "text"
        opts = [o for o in (cfg.get("options") or []) if str(o).strip()]
        if opts:
            fixed["options"] = opts
    elif v2 == "form":
        fixed["fields"] = cfg.get("fields") or []
    elif v2 == "feedback":
        fixed["dimensions"] = cfg.get("dimensions") or []
    elif v2 == "chart" and ct in ("chart.line", "chart.bar"):
        fixed["kind_default"] = "line" if ct == "chart.line" else "bar"
    return fixed


def submit_schema_for(card: dict) -> dict:
    """交互类组件提交时回传给 agent 的数据结构；展示类无提交返回 None。"""
    v2 = V2_TYPE_MAP.get(card.get("component_type") or "")
    if v2 == "select":
        return {"selected": "string[] 用户勾选的选项文本"}
    if v2 == "form":
        return {"values": "object 按字段 key 的填写值"}
    if v2 == "confirm":
        return {"confirmed": "boolean 用户是否确认"}
    if v2 == "feedback":
        return {"votes": "object 各维度的 up/down"}
    if v2 == "preference":
        return {"chosen": "string 被采纳候选的 label"}
    return None


def build_registry(product: dict) -> dict:
    """按产品输出组件注册 JSON。agent 后端拉取后作为工具声明给大模型。"""
    conn = db.get_conn()
    card_ids = db.dj(product.get("card_ids"), []) or []
    comps = []
    for cid in card_ids:
        row = conn.execute("SELECT * FROM cards WHERE card_id=?", (cid,)).fetchone()
        if not row:
            continue
        card = dict(row)
        card["field_bindings"] = db.dj(card.get("field_bindings"), {})
        ct = card.get("component_type") or ""
        if ct not in V2_ALLOWED_CT or card.get("status") != "published":
            continue
        v2 = V2_TYPE_MAP[ct]
        meta = V2_META[v2]
        comps.append({
            "component_id": card["card_id"],
            "type": v2,
            "name": card.get("name") or meta["label"],
            "description": (card.get("description") or "").strip() or meta["desc"],
            "interactive": meta["interactive"],
            "params_schema": params_schema_for(card),
            "fixed": fixed_config_for(card),
            "submit_schema": submit_schema_for(card),
        })
    return {
        "registry_version": "2.0",
        "product_id": product.get("product_id"),
        "product_name": product.get("name"),
        "brand_file": product.get("brand_file"),
        "usage": "把 components 作为工具声明给大模型：模型返回 {component_id, params}；"
                 "前端 SDK 按 component_id 渲染并在提交时回传 submit_schema 结构。展示类组件无提交。",
        "components": comps,
    }
