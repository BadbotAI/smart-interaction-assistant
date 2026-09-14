"""组件数据分析：只做「事件表里真实算得出来」的指标，不造数。

数据源是 events 表（SDK 通过 /v1/events 回传）：
  card_rendered            组件被渲染（曝光）
  card_interaction_started 用户开始操作（点选项 / 聚焦输入框）
  card_submitted           用户提交
  card_abandoned           渲染后离开且未提交
  feedback_given           赞踩反馈

由此可得四类问题的答案：
  1. 组件到底有没有被用起来       → 曝光 / 交互率 / 完成率 漏斗
  2. 哪类组件更容易被完成         → 按组件类型对比
  3. 用户到底选了什么             → 选项分布、AI 推荐项采纳率
  4. 哪些实例需要优化             → 曝光高但完成率低的实例榜
"""
import datetime as _dt
import json

from . import db

EV_RENDER = "card_rendered"
EV_START = "card_interaction_started"
EV_SUBMIT = "card_submitted"
EV_ABANDON = "card_abandoned"
EV_FEEDBACK = "feedback_given"
EV_CONTROL = "control_invoked"

# 展示类组件没有提交按钮，完成率这一列对它们没有意义（显示 0% 会被误读成"效果差"）
PRESENT_TYPES = {"table", "chart.line", "chart.bar", "chart.pie", "chart.waterfall", "chart.area",
                 "metric.card", "timeline", "steps", "matrix.compare", "list.ordered", "text.emphasis"}


def _range(days=None, start=None, end=None):
    """时间范围：给了起止日期就用日期，否则回落到「近 N 天」。"""
    if start or end:
        def _ts(d, end_of_day=False):
            try:
                t = _dt.datetime.strptime(d, "%Y-%m-%d")
            except Exception:
                return None
            if end_of_day:
                t += _dt.timedelta(days=1)
            return int(t.timestamp())
        a = _ts(start) if start else 0
        b = _ts(end, True) if end else db.now_ts() + 86400
        return a or 0, b
    n = int(days or 30)
    return db.now_ts() - n * 86400, db.now_ts() + 86400


def _rows(days=None, start=None, end=None):
    conn = db.get_conn()
    a, b = _range(days, start, end)
    return conn.execute(
        "SELECT event_type, card, payload, user_id, ts FROM events "
        "WHERE ts >= ? AND ts < ? AND admitted=1 AND COALESCE(channel,'')!='test' ORDER BY ts", (a, b)
    ).fetchall()


def _product_cards(product_id: str):
    """产品与组件的关系挂在 products.card_ids 上，事件本身不带产品。"""
    if not product_id:
        return None
    conn = db.get_conn()
    r = conn.execute("SELECT card_ids FROM products WHERE product_id=?", (product_id,)).fetchone()
    if not r:
        return set()
    return set(db.dj(r["card_ids"], []) or [])


def _filtered(days=None, start=None, end=None, card_id=None, ct=None, product=None):
    ev = _parse(_rows(days, start, end))
    if card_id:
        ev = [e for e in ev if e["card_id"] == card_id]
    if ct:
        ev = [e for e in ev if e["ct"] == ct]
    if product:
        ids = _product_cards(product)
        ev = [e for e in ev if e["card_id"] in ids]
    return ev


def _parse(rows):
    out = []
    for r in rows:
        card = db.dj(r["card"], {}) or {}
        payload = db.dj(r["payload"], {}) or {}
        out.append({
            "type": r["event_type"],
            "ct": card.get("component_type") or "",
            "card_id": card.get("card_id"),
            "cat": card.get("semantic_category") or "",
            "payload": payload,
            "user_id": r["user_id"],
            "ts": r["ts"],
        })
    return out


def _rate(a, b):
    return round(a / b * 100, 1) if b else 0.0


def _response_value(event):
    """把不同控件的终态事件统一成「有效响应」。

    采集类走 card_submitted，赞踩走 feedback_given，确认器走
    control_invoked。以前只认第一种，会让已开启群体回显的赞踩/确认器
    在分析页上仍显示 0 提交。
    """
    payload = event.get("payload") or {}
    ct = event.get("ct") or ""
    typ = event.get("type")
    if typ == EV_SUBMIT:
        return payload.get("user_selection")
    if typ == EV_FEEDBACK and ct == "feedback.binary":
        value = payload.get("value")
        return {"up": "赞", "down": "踩"}.get(value)
    if typ == EV_FEEDBACK and ct == "feedback.preference":
        return payload.get("selected_model_id")
    if typ == EV_CONTROL and ct == "control.confirm":
        return {"confirm": "确认", "cancel": "取消"}.get(payload.get("action"))
    return None


def _is_response(event):
    return _response_value(event) is not None


def _card_info(card_id, cache):
    if not card_id:
        return None
    if card_id not in cache:
        row = db.get_conn().execute(
            "SELECT name, component_type, echo_results, field_bindings FROM cards WHERE card_id=?",
            (card_id,)).fetchone()
        cache[card_id] = dict(row) if row else None
        if cache[card_id]:
            cache[card_id]["config"] = (db.dj(cache[card_id].get("field_bindings"), {}) or {}).get("config") or {}
    return cache[card_id]


def _offered_values(event, card_info=None):
    offered = event["payload"].get("options_offered")
    if offered:
        return [str(x) for x in offered]
    ct = event["ct"]
    if ct == "feedback.binary":
        return ["赞", "踩"]
    if ct == "control.confirm":
        return ["确认", "取消"]
    cfg = (card_info or {}).get("config") or {}
    values = cfg.get("options") or cfg.get("items") or []
    return [str(x) for x in values]


def overview(days=30, start=None, end=None, card_id=None, ct=None, product=None) -> dict:
    ev = _filtered(days, start, end, card_id, ct, product)
    n = lambda t: sum(1 for e in ev if e["type"] == t)
    rendered, started = n(EV_RENDER), n(EV_START)
    submitted = sum(1 for e in ev if _is_response(e))
    # 推荐项采纳：modified_from_default=False 表示用户接受了 AI 的推荐
    withrec = [e for e in ev if _is_response(e) and "modified_from_default" in e["payload"]]
    accepted = sum(1 for e in withrec if not e["payload"].get("modified_from_default"))
    # 按天趋势
    daily = {}
    for e in ev:
        if e["type"] != EV_RENDER and not _is_response(e):
            continue
        day = db.day_str(e["ts"])
        d = daily.setdefault(day, {"rendered": 0, "submitted": 0})
        d["rendered" if e["type"] == EV_RENDER else "submitted"] += 1
    trend = [{"day": k, **v} for k, v in sorted(daily.items())]
    return {
        "days": days,
        "funnel": [
            {"step": "曝光", "key": "rendered", "value": rendered, "drop_rate": _rate(rendered - started, rendered)},
            {"step": "开始操作", "key": "started", "value": started, "drop_rate": _rate(started - submitted, started)},
            {"step": "有效响应", "key": "submitted", "value": submitted, "drop_rate": None},
        ],
        "kpi": {
            "rendered": rendered,
            "submitted": submitted,
            "complete_rate": _rate(submitted, rendered),
            "interact_rate": _rate(started, rendered),
            "rec_accept_rate": _rate(accepted, len(withrec)) if withrec else None,
            "rec_sample": len(withrec),
        },
        "trend": trend,
    }


def by_type(days=30, start=None, end=None, product=None) -> dict:
    ev = _filtered(days, start, end, product=product)
    agg = {}
    for e in ev:
        if not e["ct"]:
            continue
        a = agg.setdefault(e["ct"], {"component_type": e["ct"], "rendered": 0, "started": 0,
                                     "submitted": 0, "abandoned": 0})
        if e["type"] == EV_RENDER:
            a["rendered"] += 1
        elif e["type"] == EV_START:
            a["started"] += 1
        elif _is_response(e):
            a["submitted"] += 1
        elif e["type"] == EV_ABANDON:
            a["abandoned"] += 1
    out = []
    for a in agg.values():
        a["interactive"] = a["component_type"] not in PRESENT_TYPES
        a["complete_rate"] = _rate(a["submitted"], a["rendered"]) if a["interactive"] else None
        a["interact_rate"] = _rate(a["started"], a["rendered"])
        out.append(a)
    out.sort(key=lambda x: -x["rendered"])
    return {"days": days, "rows": out}


def options(days=30, limit: int = 6, start=None, end=None, card_id=None, ct=None, product=None,
            echo_only=False, group_by_card=False) -> dict:
    """响应分布：同时覆盖提交型、赞踩和确认型控件。"""
    ev = _filtered(days, start, end, card_id, ct, product)
    groups = {}
    card_cache = {}
    for e in ev:
        # 结构化表单的回答可能包含联系人等字段，只统计提交量，不进入选项分布，
        # 避免把字段原文展示在运营页面。
        if e.get("ct") == "form.structured":
            continue
        sel = _response_value(e)
        if sel is None:
            continue
        info = _card_info(e["card_id"], card_cache)
        if echo_only and not (info and info.get("echo_results")):
            continue
        offered = [str(x) for x in _offered_values(e, info)]
        selected = [str(x) for x in (sel if isinstance(sel, list) else [sel])]
        # 固定回显控件可从实例配置补齐候选项；存量事件即使没有
        # options_offered，也不应把真实回答丢掉。
        if not offered:
            offered = list(selected)
        else:
            for value in selected:
                if value not in offered:
                    offered.append(value)
        key = ((e["card_id"] or "", e["ct"]) if group_by_card else
               (e["card_id"] or "", e["ct"], json.dumps(offered, ensure_ascii=False, sort_keys=True)))
        recommended = e["payload"].get("recommended_default")
        recommended = str(recommended) if recommended is not None else None
        g = groups.setdefault(key, {"component_type": e["ct"], "options": [],
                                    "card_id": e["card_id"],
                                    "card_name": (info or {}).get("name"),
                                    "echo_results": bool((info or {}).get("echo_results")),
                                    "counts": {}, "total": 0, "rec": recommended})
        if g["rec"] is None and recommended is not None:
            g["rec"] = recommended
        for option in offered:
            if option not in g["options"]:
                g["options"].append(option)
        for s in selected:
            g["counts"][s] = g["counts"].get(s, 0) + 1
        g["total"] += 1
    rows = sorted(groups.values(), key=lambda g: -g["total"])[:limit]
    for g in rows:
        g["dist"] = sorted(
            ({"label": o, "count": g["counts"].get(str(o), 0),
              "pct": _rate(g["counts"].get(str(o), 0), g["total"]),
              "recommended": o == g.get("rec")} for o in g["options"]),
            key=lambda x: -x["count"])
        g.pop("counts", None)
    return {"days": days, "groups": rows}


def echo_analysis(days=30, start=None, end=None, card_id=None, ct=None, product=None) -> dict:
    """群体回显专项分析。

    当前事件契约没有单独的 ``echo_rendered`` 事件，因此这里把「有效响应后具备
    回显条件」记为回显触发，不把它包装成已成功展示。这样既能统计运营效果，
    又不会凭空制造前端展示成功率。
    """
    conn = db.get_conn()
    product_ids = _product_cards(product) if product else None
    cards_rows = conn.execute(
        "SELECT card_id, name, component_type, status FROM cards "
        "WHERE echo_results=1 AND status!='deleted' ORDER BY updated_at DESC"
    ).fetchall()
    configured = []
    for row in cards_rows:
        item = dict(row)
        if card_id and item["card_id"] != card_id:
            continue
        if ct and item["component_type"] != ct:
            continue
        if product_ids is not None and item["card_id"] not in product_ids:
            continue
        configured.append(item)

    ids = {c["card_id"] for c in configured}
    agg = {
        c["card_id"]: {
            **c, "rendered": 0, "started": 0, "responses": 0,
            "respondents_set": set(), "selection_counts": {},
        }
        for c in configured
    }
    all_respondents = set()
    for event in _filtered(days, start, end, card_id, ct, product):
        cid = event["card_id"]
        if cid not in ids:
            continue
        row = agg[cid]
        if event["type"] == EV_RENDER:
            row["rendered"] += 1
        elif event["type"] == EV_START:
            row["started"] += 1
        if not _is_response(event):
            continue
        row["responses"] += 1
        if event.get("user_id"):
            row["respondents_set"].add(event["user_id"])
            all_respondents.add(event["user_id"])
        selected = _response_value(event)
        selected = selected if isinstance(selected, list) else [selected]
        for value in selected:
            # 开放文本不做 top1 排名，避免把用户原文暴露到运营榜单。
            if isinstance(value, str) and len(value) > 24:
                continue
            key = str(value)
            row["selection_counts"][key] = row["selection_counts"].get(key, 0) + 1

    rows = []
    consensus_top = 0
    consensus_base = 0
    for row in agg.values():
        counts = row.pop("selection_counts")
        respondents = len(row.pop("respondents_set"))
        top_choice, top_count = (max(counts.items(), key=lambda x: x[1]) if counts else (None, 0))
        row.update({
            "respondents": respondents,
            "echo_triggers": row["responses"],
            "echo_rate": _rate(row["responses"], row["rendered"]),
            "top_choice": top_choice,
            "consensus_rate": _rate(top_count, row["responses"]) if counts else None,
            "divergence_rate": round(100 - _rate(top_count, row["responses"]), 1) if counts else None,
        })
        if counts and row["responses"]:
            consensus_top += top_count
            consensus_base += row["responses"]
        rows.append(row)

    rows.sort(key=lambda x: (-x["echo_triggers"], -x["rendered"], x["name"]))
    rendered = sum(r["rendered"] for r in rows)
    responses = sum(r["responses"] for r in rows)
    return {
        "days": days,
        "kpi": {
            "enabled_instances": len(configured),
            "rendered": rendered,
            "echo_triggers": responses,
            "echo_rate": _rate(responses, rendered),
            "respondents": len(all_respondents),
            "consensus_rate": _rate(consensus_top, consensus_base) if consensus_base else None,
            "divergence_rate": round(100 - _rate(consensus_top, consensus_base), 1) if consensus_base else None,
        },
        "rows": rows,
        "measurement_note": "回显触发按开启回显控件的有效响应计；当前事件契约未采集前端回显展示成功事件。",
    }


def instances(days=30, limit: int = 12, start=None, end=None, ct=None, product=None) -> dict:
    """实例榜：带 card_id 的事件才能归到具体实例；关联卡片名与状态。"""
    ev = _filtered(days, start, end, ct=ct, product=product)
    agg = {}
    for e in ev:
        cid = e["card_id"]
        if not cid:
            continue
        a = agg.setdefault(cid, {"card_id": cid, "rendered": 0, "submitted": 0, "component_type": e["ct"]})
        if e["type"] == EV_RENDER:
            a["rendered"] += 1
        elif _is_response(e):
            a["submitted"] += 1
    conn = db.get_conn()
    rows = []
    for a in agg.values():
        c = conn.execute("SELECT name, status FROM cards WHERE card_id=?", (a["card_id"],)).fetchone()
        if not c:
            continue   # 实例已删除，排行里留一行「（已删除）」没有意义
        a["name"] = c["name"]
        a["status"] = c["status"]
        a["interactive"] = a["component_type"] not in PRESENT_TYPES
        a["complete_rate"] = _rate(a["submitted"], a["rendered"]) if a["interactive"] else None
        rows.append(a)
    rows.sort(key=lambda x: -x["rendered"])
    return {"days": days, "rows": rows[:limit]}


def filters() -> dict:
    """筛选器选项：只列真正产生过事件的实例，避免一长串空选项。"""
    conn = db.get_conn()
    ev = _parse(_rows(365))
    seen_ct, seen_card = {}, {}
    for e in ev:
        if e["ct"]:
            seen_ct[e["ct"]] = seen_ct.get(e["ct"], 0) + 1
        if e["card_id"]:
            seen_card[e["card_id"]] = seen_card.get(e["card_id"], 0) + 1
    product_ids_by_card = {}
    for p in conn.execute("SELECT product_id, card_ids FROM products").fetchall():
        for cid in (db.dj(p["card_ids"], []) or []):
            product_ids_by_card.setdefault(cid, []).append(p["product_id"])
    cards_out = []
    for cid, n in sorted(seen_card.items(), key=lambda kv: -kv[1]):
        r = conn.execute(
            "SELECT name, component_type, status, echo_results FROM cards WHERE card_id=?", (cid,)
        ).fetchone()
        if not r:
            continue
        cards_out.append({"card_id": cid, "name": r["name"], "component_type": r["component_type"],
                          "status": r["status"], "echo_results": bool(r["echo_results"]),
                          "product_ids": product_ids_by_card.get(cid, []), "events": n})
    prods = []
    for r in conn.execute("SELECT product_id, name, card_ids FROM products ORDER BY created_at").fetchall():
        ids_list = db.dj(r["card_ids"], []) or []
        ids = set(ids_list)
        hits = sum(n for cid, n in seen_card.items() if cid in ids)
        prods.append({"product_id": r["product_id"], "name": r["name"],
                      "card_ids": ids_list, "events": hits})
    return {
        "products": prods,
        "component_types": [{"component_type": k, "events": v}
                            for k, v in sorted(seen_ct.items(), key=lambda kv: -kv[1])],
        "cards": cards_out,
    }
