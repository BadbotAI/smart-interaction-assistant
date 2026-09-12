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
        "SELECT event_type, card, payload, ts FROM events WHERE ts >= ? AND ts < ? ORDER BY ts", (a, b)
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
            "ts": r["ts"],
        })
    return out


def _rate(a, b):
    return round(a / b * 100, 1) if b else 0.0


def overview(days=30, start=None, end=None, card_id=None, ct=None, product=None) -> dict:
    ev = _filtered(days, start, end, card_id, ct, product)
    n = lambda t: sum(1 for e in ev if e["type"] == t)
    rendered, started, submitted = n(EV_RENDER), n(EV_START), n(EV_SUBMIT)
    # 推荐项采纳：modified_from_default=False 表示用户接受了 AI 的推荐
    withrec = [e for e in ev if e["type"] == EV_SUBMIT and "modified_from_default" in e["payload"]]
    accepted = sum(1 for e in withrec if not e["payload"].get("modified_from_default"))
    # 按天趋势
    daily = {}
    for e in ev:
        if e["type"] not in (EV_RENDER, EV_SUBMIT):
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
            {"step": "提交", "key": "submitted", "value": submitted, "drop_rate": None},
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
        elif e["type"] == EV_SUBMIT:
            a["submitted"] += 1
        elif e["type"] == EV_ABANDON:
            a["abandoned"] += 1
    out = []
    for a in agg.values():
        a["complete_rate"] = _rate(a["submitted"], a["rendered"])
        a["interact_rate"] = _rate(a["started"], a["rendered"])
        out.append(a)
    out.sort(key=lambda x: -x["rendered"])
    return {"days": days, "rows": out}


def options(days=30, limit: int = 6, start=None, end=None, card_id=None, ct=None, product=None) -> dict:
    """选项分布：只统计提交事件里带 options_offered 的选择型组件。"""
    ev = _filtered(days, start, end, card_id, ct, product)
    groups = {}
    for e in ev:
        if e["type"] != EV_SUBMIT:
            continue
        offered = e["payload"].get("options_offered")
        sel = e["payload"].get("user_selection")
        if not offered or sel is None:
            continue
        key = (e["ct"], json.dumps(offered, ensure_ascii=False, sort_keys=True))
        g = groups.setdefault(key, {"component_type": e["ct"], "options": offered,
                                    "counts": {}, "total": 0, "rec": e["payload"].get("recommended_default")})
        for s in (sel if isinstance(sel, list) else [sel]):
            g["counts"][str(s)] = g["counts"].get(str(s), 0) + 1
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
        elif e["type"] == EV_SUBMIT:
            a["submitted"] += 1
    conn = db.get_conn()
    rows = []
    for a in agg.values():
        c = conn.execute("SELECT name, status FROM cards WHERE card_id=?", (a["card_id"],)).fetchone()
        a["name"] = c["name"] if c else "（已删除）"
        a["status"] = c["status"] if c else "deleted"
        a["complete_rate"] = _rate(a["submitted"], a["rendered"])
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
    cards_out = []
    for cid, n in sorted(seen_card.items(), key=lambda kv: -kv[1]):
        r = conn.execute("SELECT name, component_type, status FROM cards WHERE card_id=?", (cid,)).fetchone()
        if not r:
            continue
        cards_out.append({"card_id": cid, "name": r["name"], "component_type": r["component_type"],
                          "status": r["status"], "events": n})
    prods = []
    for r in conn.execute("SELECT product_id, name, card_ids FROM products ORDER BY created_at").fetchall():
        ids = set(db.dj(r["card_ids"], []) or [])
        hits = sum(n for cid, n in seen_card.items() if cid in ids)
        prods.append({"product_id": r["product_id"], "name": r["name"], "events": hits})
    return {
        "products": prods,
        "component_types": [{"component_type": k, "events": v}
                            for k, v in sorted(seen_ct.items(), key=lambda kv: -kv[1])],
        "cards": cards_out,
    }
