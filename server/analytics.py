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
import json

from . import db

EV_RENDER = "card_rendered"
EV_START = "card_interaction_started"
EV_SUBMIT = "card_submitted"
EV_ABANDON = "card_abandoned"
EV_FEEDBACK = "feedback_given"


def _rows(days: int):
    conn = db.get_conn()
    since = db.now_ts() - days * 86400
    return conn.execute(
        "SELECT event_type, card, payload, ts FROM events WHERE ts >= ? ORDER BY ts", (since,)
    ).fetchall()


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


def overview(days: int = 30) -> dict:
    ev = _parse(_rows(days))
    n = lambda t: sum(1 for e in ev if e["type"] == t)
    rendered, started, submitted = n(EV_RENDER), n(EV_START), n(EV_SUBMIT)
    # 决策时长：提交事件自带 time_to_submit_ms
    durs = [e["payload"].get("time_to_submit_ms") for e in ev
            if e["type"] == EV_SUBMIT and isinstance(e["payload"].get("time_to_submit_ms"), (int, float))]
    durs.sort()
    median = durs[len(durs) // 2] if durs else None
    # 推荐项采纳：modified_from_default=False 表示用户接受了 AI 的推荐
    withrec = [e for e in ev if e["type"] == EV_SUBMIT and "modified_from_default" in e["payload"]]
    accepted = sum(1 for e in withrec if not e["payload"].get("modified_from_default"))
    # 赞踩
    fb = [e for e in ev if e["type"] == EV_FEEDBACK]
    up = sum(1 for e in fb if str(e["payload"].get("value")) in ("up", "1", "1.0", "True"))
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
            {"step": "曝光", "key": "rendered", "value": rendered, "rate": 100.0},
            {"step": "开始操作", "key": "started", "value": started, "rate": _rate(started, rendered)},
            {"step": "提交", "key": "submitted", "value": submitted, "rate": _rate(submitted, rendered)},
        ],
        "kpi": {
            "rendered": rendered,
            "submitted": submitted,
            "complete_rate": _rate(submitted, rendered),
            "interact_rate": _rate(started, rendered),
            "median_submit_ms": median,
            "rec_accept_rate": _rate(accepted, len(withrec)) if withrec else None,
            "rec_sample": len(withrec),
            "feedback_total": len(fb),
            "feedback_up_rate": _rate(up, len(fb)) if fb else None,
        },
        "trend": trend,
    }


def by_type(days: int = 30) -> dict:
    ev = _parse(_rows(days))
    agg = {}
    for e in ev:
        if not e["ct"]:
            continue
        a = agg.setdefault(e["ct"], {"component_type": e["ct"], "rendered": 0, "started": 0,
                                     "submitted": 0, "abandoned": 0, "durs": []})
        if e["type"] == EV_RENDER:
            a["rendered"] += 1
        elif e["type"] == EV_START:
            a["started"] += 1
        elif e["type"] == EV_SUBMIT:
            a["submitted"] += 1
            d = e["payload"].get("time_to_submit_ms")
            if isinstance(d, (int, float)):
                a["durs"].append(d)
        elif e["type"] == EV_ABANDON:
            a["abandoned"] += 1
    out = []
    for a in agg.values():
        durs = sorted(a.pop("durs"))
        a["median_ms"] = durs[len(durs) // 2] if durs else None
        a["complete_rate"] = _rate(a["submitted"], a["rendered"])
        out.append(a)
    out.sort(key=lambda x: -x["rendered"])
    return {"days": days, "rows": out}


def options(days: int = 30, limit: int = 6) -> dict:
    """选项分布：只统计提交事件里带 options_offered 的选择型组件。"""
    ev = _parse(_rows(days))
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


def instances(days: int = 30, limit: int = 12) -> dict:
    """实例榜：带 card_id 的事件才能归到具体实例；关联卡片名与状态。"""
    ev = _parse(_rows(days))
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
