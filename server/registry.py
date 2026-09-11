"""组件注册层（v2 智能助手交互）：把产品启用的组件实例编译成给 agent 的注册 JSON。

对大模型：组件即工具——只暴露「说明 + 参数 schema」，不暴露内部排布与样式。
agent 后端拉取本注册表声明给大模型；模型返回「组件 id + 参数」；前端 SDK 按产品的
组件定义与风格主题渲染。业务逻辑全部留在 agent 侧，平台只管组件、样式与数据回收。
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
    "chart.line": "trend", "chart.area": "trend",
    "chart.bar": "bar",
    "chart.pie": "pie",
    "metric.card": "metric",
    "timeline": "timeline",
    "steps": "steps",
    "slider.range": "slider",
    "scale.likert": "rating",
    "picker.datetime": "datetime",
    "rank.priority": "rank",
    "matrix.compare": "compare",
    "list.ordered": "list",
    "text.emphasis": "highlight",
    "chart.waterfall": "waterfall",
}
V2_ALLOWED_CT = set(V2_TYPE_MAP)

# 注册类型元信息：交互类必有提交（唯一后端交互点）；展示类无提交。
# v2.1：平台只管「组件 + 样式」，说明与内容全部参数化——描述默认用类型说明，agent 侧可自行改写。
V2_META = {
    "select":     {"label": "选择器", "interactive": True,
                   "desc": "需要用户在若干候选中做选择时使用（单选或多选由参数 multi 决定）；支持 2-8 个候选项，由调用参数给出。"},
    "form":       {"label": "表单", "interactive": True,
                   "desc": "需要用户补充结构化信息时使用；字段列表由调用参数定义（key / 标签 / 是否必填）。"},
    "confirm":    {"label": "确认器", "interactive": True,
                   "desc": "执行不可逆或高风险动作前使用：用户明确点击确认 / 取消后才继续。"},
    "feedback":   {"label": "赞踩反馈", "interactive": True,
                   "desc": "对一条回答收集赞 / 踩评价，可带评价维度。"},
    "preference": {"label": "偏好选择器", "interactive": True,
                   "desc": "多个候选回答或方案让用户择优（多模型 / 多方案对比场景）。"},
    "table":      {"label": "表格", "interactive": False,
                   "desc": "要表达清单、对比、多行记录等结构化数据时使用，替代大段文字。"},
    "trend":      {"label": "折线图", "interactive": False,
                   "desc": "要表达数列随时间的走势时使用（折线图）。"},
    "bar":        {"label": "柱状图", "interactive": False,
                   "desc": "要表达类别之间的数量对比或分布时使用（柱状图）。"},
    "pie":        {"label": "饼图", "interactive": False,
                   "desc": "要表达部分与整体的占比构成时使用（百分比条）。"},
    "metric":     {"label": "指标卡", "interactive": False,
                   "desc": "要突出一个关键数字（含涨跌与基线）时使用。"},
    "timeline":   {"label": "时间线", "interactive": False,
                   "desc": "要表达事件先后过程、里程碑或进度时使用。"},
    "steps":      {"label": "步骤条", "interactive": False,
                   "desc": "要给出分步操作指引并标注当前进行到哪一步时使用。"},
    "slider":     {"label": "数值滑杆", "interactive": True,
                   "desc": "需要用户给出一个范围内的数值时使用（预算、数量、额度），范围与步长由调用参数给出。"},
    "rating":     {"label": "评分量表", "interactive": True,
                   "desc": "需要用户按刻度打分时使用（满意度、意愿度），刻度档数由调用参数给出。"},
    "datetime":   {"label": "日期时间", "interactive": True,
                   "desc": "需要用户选择日期或时间时使用（预约、提醒、截止时间）。"},
    "rank":       {"label": "排序器", "interactive": True,
                   "desc": "需要用户对若干条目按重要程度排序时使用，条目由调用参数给出。"},
    "compare":    {"label": "对比矩阵", "interactive": False,
                   "desc": "要把多个方案按多个维度打分对比时使用（矩阵表 + 综合分）。"},
    "list":       {"label": "要点清单", "interactive": False,
                   "desc": "要按序列出要点、结论或注意事项时使用（无列结构的条目列举）。"},
    "highlight":  {"label": "重点结论", "interactive": False,
                   "desc": "要用一句话突出核心结论或状态时使用（可带正负语气）。"},
    "waterfall":  {"label": "瀑布图", "interactive": False,
                   "desc": "要表达一个数值如何被多个增减项逐步构成时使用（成本拆解、变化归因）。"},
}

# 每类的默认存储 component_type（实例创建与渲染入口）
V2_DEFAULT_CT = {"select": "select.single", "form": "form.structured", "confirm": "control.confirm",
                 "feedback": "feedback.binary", "preference": "feedback.preference",
                 "table": "table", "trend": "chart.line", "bar": "chart.bar", "pie": "chart.pie",
                 "metric": "metric.card", "timeline": "timeline", "steps": "steps",
                 "slider": "slider.range", "rating": "scale.likert", "datetime": "picker.datetime",
                 "rank": "rank.priority", "compare": "matrix.compare", "list": "list.ordered",
                 "highlight": "text.emphasis", "waterfall": "chart.waterfall"}


def _cfg(card: dict) -> dict:
    return ((card.get("field_bindings") or {}).get("config") or {})


def params_schema_for(card: dict) -> dict:
    """模型调用该组件实例时要填的参数（JSON Schema 子集）。
    v2.1：平台不配内容——说明与内容全部参数化，实例只携带样式。"""
    ct = card.get("component_type") or ""
    v2 = V2_TYPE_MAP.get(ct)
    cfg = _cfg(card)
    fixed_mode = cfg.get("content_mode") == "fixed"
    schema = {"type": "object", "properties": {}, "required": []}
    props, req = schema["properties"], schema["required"]
    S = lambda d: {"type": "string", "description": d}
    if fixed_mode and not (V2_META.get(v2) or {}).get("interactive", True):
        schema["description"] = "内容已由平台固定配置，模型只需选用本组件，无需传数据参数"
        return schema
    if v2 == "select":
        props["prompt"] = S("向用户提出的问题")
        if fixed_mode:
            # 固定组件：选项由平台定死（业务定义），模型不可覆盖
            req.append("prompt")
        else:
            props["options"] = {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 8,
                                "description": "候选项文本，按当前对话给出"}
            req.extend(["prompt", "options"])
        props["multi"] = {"type": "boolean", "description": "true=多选（勾选后提交），false=单选"}
    elif v2 == "form" and fixed_mode:
        props["prompt"] = S("表单引导语")
        props["prefill"] = {"type": "object", "description": "可选：按字段 key 预填已知值"}
        req.append("prompt")
    elif v2 == "form":
        props["prompt"] = S("表单引导语")
        props["fields"] = {"type": "array", "minItems": 1, "maxItems": 8,
                           "items": {"type": "object",
                                     "properties": {"key": S("字段标识（英文）"), "label": S("字段名"),
                                                    "required": {"type": "boolean"}},
                                     "required": ["key", "label"]},
                           "description": "要收集的字段列表"}
        props["prefill"] = {"type": "object", "description": "可选：按字段 key 预填已知值"}
        req.extend(["prompt", "fields"])
    elif v2 == "confirm":
        props["prompt"] = S("需要用户确认的动作描述")
        props["summary"] = S("可选：动作影响的一句话摘要")
        req.append("prompt")
    elif v2 == "feedback":
        props["prompt"] = S("可选：评价引导语")
        props["dimensions"] = {"type": "array", "maxItems": 4,
                               "items": {"type": "object",
                                         "properties": {"key": S("维度标识"), "label": S("维度名")},
                                         "required": ["key", "label"]},
                               "description": "可选：评价维度，缺省为单一赞踩"}
    elif v2 == "preference":
        props["candidates"] = {"type": "array", "minItems": 2, "maxItems": 4,
                               "items": {"type": "object",
                                         "properties": {"label": S("候选名"), "content": S("候选内容")},
                                         "required": ["label", "content"]},
                               "description": "候选回答列表，用户从中择优"}
        req.append("candidates")
    elif v2 == "table":
        props["title"] = S("表格标题，可选")
        props["columns"] = {"type": "array", "items": {"type": "string"}, "minItems": 2}
        props["rows"] = {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}
        req.extend(["columns", "rows"])
    elif v2 in ("trend", "bar"):
        props["title"] = S("图表标题，可选")
        props["categories"] = {"type": "array", "items": {"type": "string"}, "description": "横轴类目"}
        props["series"] = {"type": "array",
                           "items": {"type": "object",
                                     "properties": {"name": S("系列名"),
                                                    "values": {"type": "array", "items": {"type": "number"}}},
                                     "required": ["name", "values"]}}
        req.extend(["categories", "series"])
    elif v2 == "pie":
        props["title"] = S("标题，可选")
        props["slices"] = {"type": "array", "minItems": 2, "maxItems": 7,
                           "items": {"type": "object",
                                     "properties": {"label": S("类别名"), "value": {"type": "number"}},
                                     "required": ["label", "value"]}}
        req.append("slices")
    elif v2 == "metric":
        props["label"] = S("指标名")
        props["value"] = S("指标值（数字或文本）")
        props["unit"] = S("单位，可选")
        props["delta"] = S("涨跌值，可选，负数下跌")
        props["baseline"] = S("基线说明，可选")
        req.extend(["label", "value"])
    elif v2 == "timeline":
        props["title"] = S("标题，可选")
        props["events"] = {"type": "array", "minItems": 2, "maxItems": 12,
                           "items": {"type": "object",
                                     "properties": {"ts": S("时间"), "title": S("事件"), "desc": S("说明，可选")},
                                     "required": ["title"]}}
        req.append("events")
    elif v2 == "steps":
        props["title"] = S("标题，可选")
        props["steps"] = {"type": "array", "minItems": 2, "maxItems": 9,
                          "items": {"type": "string"}, "description": "步骤文本列表"}
        props["current_index"] = {"type": "integer", "description": "当前进行到第几步（0 起）"}
        req.append("steps")
    elif v2 == "slider":
        props["prompt"] = S("向用户提出的问题")
        props["min"] = {"type": "number", "description": "最小值"}
        props["max"] = {"type": "number", "description": "最大值"}
        props["step"] = {"type": "number", "description": "步长，可选，默认 1"}
        props["unit"] = S("单位，可选（元 / 件 / 天）")
        props["default"] = {"type": "number", "description": "初始值，可选"}
        req.extend(["prompt", "min", "max"])
    elif v2 == "rating":
        props["prompt"] = S("评分引导语")
        props["scale"] = {"type": "integer", "minimum": 2, "maximum": 11, "description": "刻度档数（5 或 10 常用）"}
        props["low_label"] = S("低端含义，可选（如 很不满意）")
        props["high_label"] = S("高端含义，可选（如 非常满意）")
        req.extend(["prompt", "scale"])
    elif v2 == "datetime":
        props["prompt"] = S("选择引导语")
        props["mode"] = {"type": "string", "enum": ["date", "datetime"], "description": "选日期还是日期+时间"}
        req.append("prompt")
    elif v2 == "rank":
        props["prompt"] = S("排序引导语")
        if fixed_mode:
            req.append("prompt")
        else:
            props["items"] = {"type": "array", "minItems": 2, "maxItems": 8, "items": {"type": "string"},
                              "description": "待排序条目，按当前对话给出"}
            req.extend(["prompt", "items"])
    elif v2 == "compare":
        props["title"] = S("标题，可选")
        props["options"] = {"type": "array", "minItems": 2, "maxItems": 5, "items": {"type": "string"},
                            "description": "方案名列表"}
        props["dimensions"] = {"type": "array", "minItems": 1, "maxItems": 6, "items": {"type": "string"},
                               "description": "对比维度列表"}
        props["values"] = {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
                           "description": "打分矩阵：每个方案一行，与 dimensions 对齐"}
        req.extend(["options", "dimensions", "values"])
    elif v2 == "list":
        props["title"] = S("标题，可选")
        props["items"] = {"type": "array", "minItems": 2, "maxItems": 12, "items": {"type": "string"},
                          "description": "要点条目列表"}
        req.append("items")
    elif v2 == "waterfall":
        props["title"] = S("标题，可选")
        props["categories"] = {"type": "array", "minItems": 2, "maxItems": 10, "items": {"type": "string"},
                               "description": "增减项名称（首项通常为起始值）"}
        props["values"] = {"type": "array", "items": {"type": "number"},
                           "description": "各项增减量（正增负减），与 categories 对齐"}
        req.extend(["categories", "values"])
    elif v2 == "highlight":
        props["value"] = S("要突出的结论文本")
        props["caption"] = S("补充说明，可选")
        props["tone"] = {"type": "string", "enum": ["positive", "neutral", "negative"], "description": "语气，可选"}
        req.append("value")
    return schema


def fixed_config_for(card: dict) -> dict:
    """平台侧固定项：v2.1 内容全参数化后，只剩样式相关（选择表单的展现样式变体等）。"""
    ct = card.get("component_type") or ""
    cfg = _cfg(card)
    v2 = V2_TYPE_MAP.get(ct)
    fixed = {}
    if v2 == "select":
        fixed["display"] = "card" if (ct == "select.card" or cfg.get("display") == "card") else "text"
    if cfg.get("content_mode") == "fixed":
        # 固定组件：业务内容平台定死，随注册表下发（模型只读）
        fixed["content_mode"] = "fixed"
        if v2 == "select" and cfg.get("options"):
            fixed["options"] = cfg.get("options")
        if v2 == "form" and cfg.get("fields"):
            fixed["fields"] = cfg.get("fields")
        if v2 == "rank" and cfg.get("options"):
            fixed["items"] = cfg.get("options")
        if cfg.get("recommended_default"):
            fixed["recommended_default"] = cfg.get("recommended_default")
        if cfg.get("present_params") and not (V2_META.get(v2) or {}).get("interactive", True):
            fixed["present_params"] = cfg.get("present_params")
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
    if v2 == "slider":
        return {"value": "number 用户选定的数值"}
    if v2 == "rating":
        return {"score": "number 用户打出的分数"}
    if v2 == "datetime":
        return {"datetime": "string 用户选择的日期时间（ISO）"}
    if v2 == "rank":
        return {"ranked": "string[] 按用户排序后的条目"}
    return None


def build_registry(product: dict) -> dict:
    """按产品输出组件注册 JSON。agent 后端拉取后作为工具声明给大模型。"""
    conn = db.get_conn()
    card_ids = db.dj(product.get("card_ids"), []) or []
    comps = []
    style_map = {}
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
        _so = db.dj(card.get("style_overrides"), {}) if isinstance(card.get("style_overrides"), str) else (card.get("style_overrides") or {})
        if _so:
            style_map[cid] = _so
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
    import hashlib as _h
    import time as _t
    # 指纹覆盖「出包内容」全量：schema / fixed 之外还含组件级样式与产品品牌——任一变化 = 线上包需重新拉取部署
    body_key = _h.md5(repr((product.get("brand_file"), sorted(style_map.items()),
        sorted((c["component_id"], c["name"], str(c["params_schema"]), str(c["fixed"])) for c in comps))).encode()).hexdigest()[:10]
    return {
        "registry_version": "2.1",
        "generated_at": _t.strftime("%Y-%m-%dT%H:%M:%S"),
        "content_hash": body_key,
        "product_id": product.get("product_id"),
        "product_name": product.get("name"),
        "brand_file": product.get("brand_file"),
        "usage": "把 components 作为工具声明给大模型：模型返回 {component_id, params}；"
                 "前端 SDK 按 component_id 渲染并在提交时回传 submit_schema 结构。展示类组件无提交。",
        "components": comps,
    }


def build_catalog() -> list:
    """组件库目录：7 类泛化组件模板（无实例语境下的 schema 示例，供组件库页展示与新建选型）。"""
    out = []
    for v2, meta in V2_META.items():
        stub = {"component_type": V2_DEFAULT_CT[v2], "field_bindings": {"config": {}}}
        out.append({"type": v2, "label": meta["label"], "desc": meta["desc"],
                    "interactive": meta["interactive"],
                    "params_schema": params_schema_for(stub),
                    "submit_schema": submit_schema_for(stub),
                    "component_types": [V2_DEFAULT_CT[v2]]})
    return out
