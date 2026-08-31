"""P3 看板聚合（§4.2 A/B 两类视角 + bank 健康度 + A/B 对比）。"""
import math
import time
from collections import defaultdict

from . import db


def _pct(values, p):
    if not values:
        return 0
    values = sorted(values)
    idx = min(len(values) - 1, int(len(values) * p / 100))
    return values[idx]


def _entropy(counts):
    total = sum(counts.values())
    if total == 0:
        return 0.0
    h = 0.0
    for c in counts.values():
        if c > 0:
            p = c / total
            h -= p * math.log2(p)
    return round(h, 3)


def overview(days: int = 7, mode: str = None, model: str = None, status: str = None):
    """A 类：成本与运行视角。支持按调用模式 / 模型 / 结果状态筛选（标书 F-5-05）。"""
    conn = db.get_conn()
    since = time.time() - days * 86400

    decision_by_trace = {}
    for r in conn.execute(
            "SELECT rd.trace_id, rd.decision FROM route_decisions rd JOIN traces t ON rd.trace_id=t.trace_id "
            "WHERE t.ts>?", (since,)).fetchall():
        decision_by_trace[r["trace_id"]] = db.dj(r["decision"], {})

    all_traces = conn.execute("SELECT * FROM traces WHERE ts>?", (since,)).fetchall()
    mode_counts = defaultdict(int)
    traces_rows = []
    for t in all_traces:
        d = decision_by_trace.get(t["trace_id"]) or {}
        t_mode = d.get("mode") or "auto"
        mode_counts[t_mode] += 1
        if mode and t_mode != mode:
            continue
        if model and t["final_model"] != model:
            continue
        if status and (t["status"] or "ok") != status:
            continue
        traces_rows.append(t)

    # 聚合效果：聚合 vs 单模型的量 / 成本 / 延迟对比
    agg_n = agg_cost = agg_lat = 0
    single_n = single_lat = 0
    total_cost_all = 0.0
    for t in traces_rows:
        d = decision_by_trace.get(t["trace_id"]) or {}
        c = t["total_cost"] or 0
        total_cost_all += c
        if d.get("switch_result") == "aggregated":
            agg_n += 1; agg_cost += c; agg_lat += (t["total_latency_ms"] or 0)
        elif d.get("switch_result") in ("routed", "fastlane"):
            single_n += 1; single_lat += (t["total_latency_ms"] or 0)
    aggregation_stats = {
        "requests": agg_n,
        "cost": round(agg_cost, 6),
        "cost_share": round(agg_cost / total_cost_all, 4) if total_cost_all else None,
        "avg_latency_agg": round(agg_lat / agg_n) if agg_n else None,
        "avg_latency_single": round(single_lat / single_n) if single_n else None,
    }

    tokens = {"input": 0, "output": 0, "thinking": 0, "cache_hit": 0}
    cost_by_model = defaultdict(float)
    tokens_by_model = defaultdict(int)
    for t in traces_rows:
        d = decision_by_trace.get(t["trace_id"]) or {}
        for call in d.get("model_calls", []):
            tokens["input"] += call.get("tokens_in", 0)
            tokens["output"] += call.get("tokens_out", 0)
            tokens["thinking"] += call.get("tokens_thinking", 0)
            cost_by_model[call["model_id"]] += call.get("cost", 0)
            tokens_by_model[call["model_id"]] += call.get("tokens_in", 0) + call.get("tokens_out", 0)

    switch_counts = defaultdict(int)
    final_model_counts = defaultdict(int)
    latency_by_path = defaultdict(list)
    session_costs = defaultdict(float)
    day_costs = defaultdict(float)
    explore_count = 0
    for t in traces_rows:
        sw = t["switch_result"] or "unknown"
        switch_counts[sw] += 1
        if t["final_model"]:
            final_model_counts[t["final_model"]] += 1
        latency_by_path[sw].append(t["total_latency_ms"] or 0)
        session_costs[t["session_id"]] += t["total_cost"] or 0
        day = time.strftime("%m-%d", time.localtime(t["ts"]))
        day_costs[day] += t["total_cost"] or 0
        if t["is_explore"]:
            explore_count += 1

    total = len(traces_rows) or 1
    sc = list(session_costs.values())
    failures = {}
    for r in conn.execute(
            "SELECT span_type, COUNT(*) AS c FROM spans s JOIN traces t ON s.trace_id=t.trace_id "
            "WHERE s.status IN ('timeout','error','degraded') AND t.ts>? GROUP BY span_type", (since,)).fetchall():
        failures[r["span_type"]] = r["c"]
    for r in conn.execute(
            "SELECT event_type, COUNT(*) AS c FROM events WHERE event_type IN ('render_degraded','card_ref_missing') "
            "AND ts>? GROUP BY event_type", (since,)).fetchall():
        failures[r["event_type"]] = r["c"]

    quota_rows = [dict(r) for r in conn.execute(
        "SELECT * FROM quota_usage ORDER BY day DESC LIMIT 14").fetchall()]

    return {
        "aggregation_stats": aggregation_stats,
        "window_days": days,
        "filters": {"mode": mode, "model": model, "status": status},
        "mode_distribution": dict(sorted(mode_counts.items(), key=lambda x: -x[1])),
        "total_requests": len(traces_rows),
        "tokens": tokens,
        "cost_total": round(sum(cost_by_model.values()), 6),
        "cost_by_model": {k: round(v, 6) for k, v in sorted(cost_by_model.items(), key=lambda x: -x[1])},
        "tokens_by_model": dict(tokens_by_model),
        "cost_by_day": dict(sorted(day_costs.items())),
        "session_cost": {"p50": round(_pct(sc, 50), 6), "p90": round(_pct(sc, 90), 6),
                         "p99": round(_pct(sc, 99), 6), "histogram": _histogram(sc)},
        "route_distribution": dict(sorted(final_model_counts.items(), key=lambda x: -x[1])),
        "selection_entropy": _entropy(final_model_counts),
        "entropy_alert": _entropy(final_model_counts) < 1.0 and len(final_model_counts) > 2,
        "aggregation_rate": round(switch_counts.get("aggregated", 0) / total, 4),
        "fastlane_rate": round(switch_counts.get("fastlane", 0) / total, 4),
        "explore_rate": round(explore_count / total, 4),
        "switch_counts": dict(switch_counts),
        "latency_by_path": {k: {"p50": _pct(v, 50), "p95": _pct(v, 95), "count": len(v)}
                            for k, v in latency_by_path.items()},
        "failures": failures,
        "quota_usage": quota_rows,
    }


def _histogram(values, bins=8):
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi <= lo:
        return [{"range": f"{lo:.4f}", "count": len(values)}]
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = min(bins - 1, int((v - lo) / width))
        counts[idx] += 1
    return [{"range": f"{lo + i * width:.4f}", "count": c} for i, c in enumerate(counts)]


def insights(days: int = 30):
    """B 类：洞察视角（T+1 语义，演示环境直接现算）。"""
    conn = db.get_conn()
    since = time.time() - days * 86400
    rows = [dict(r) for r in conn.execute("SELECT * FROM events WHERE ts>?", (since,)).fetchall()]
    for r in rows:
        r["card"] = db.dj(r["card"], {})
        r["payload"] = db.dj(r["payload"], {})
        r["group_info"] = db.dj(r["group_info"])

    trigger_dist = defaultdict(lambda: defaultdict(int))
    funnel = defaultdict(int)
    funnel_by_type = defaultdict(lambda: defaultdict(int))
    option_dist = defaultdict(lambda: defaultdict(int))
    default_stats = {"accepted": 0, "modified": 0}
    group_items = []
    for r in rows:
        ct = r["card"].get("component_type") or "unknown"
        if r["event_type"] == "card_rendered":
            trigger_dist[ct][r["card"].get("trigger_source") or "unknown"] += 1
        if r["event_type"] in ("card_rendered", "card_interaction_started", "card_submitted", "card_abandoned"):
            funnel[r["event_type"]] += 1
            funnel_by_type[ct][r["event_type"]] += 1
        if r["event_type"] == "card_submitted":
            sel = r["payload"].get("user_selection")
            if sel is not None:
                option_dist[ct][str(sel)] += 1
            if "modified_from_default" in r["payload"]:
                key = "modified" if r["payload"]["modified_from_default"] else "accepted"
                default_stats[key] += 1
            g = r.get("group_info")
            if g and g.get("distribution"):
                dist = g["distribution"]
                total_votes = sum(dist.values()) or 1
                top = max(dist.values()) if dist else 0
                group_items.append({"distribution": dist, "disagreement": round(1 - top / total_votes, 3),
                                    "participants": g.get("participants_count", total_votes)})

    label_rows = [dict(r) for r in conn.execute("SELECT * FROM labels WHERE created_at>?", (since,)).fetchall()]
    label_by_day = defaultdict(lambda: defaultdict(int))
    model_quality = defaultdict(lambda: {"pos": 0, "neg": 0, "pref_win": 0, "pref_lose": 0})
    for l in label_rows:
        day = time.strftime("%m-%d", time.localtime(l["created_at"]))
        label_by_day[day][l["source"]] += 1
        mq = model_quality[l["model_id"]]
        if l["source"] == "explicit_preference":
            mq["pref_win" if l["value"] >= 0.5 else "pref_lose"] += 1
        elif l["value"] >= 0.5:
            mq["pos"] += 1
        else:
            mq["neg"] += 1

    quality_table = []
    for mid, q in model_quality.items():
        pref_total = q["pref_win"] + q["pref_lose"]
        fb_total = q["pos"] + q["neg"]
        quality_table.append({
            "model_id": mid,
            "preference_win_rate": round(q["pref_win"] / pref_total, 3) if pref_total else None,
            "downvote_rate": round(q["neg"] / fb_total, 3) if fb_total else None,
            "label_count": pref_total + fb_total,
        })
    quality_table.sort(key=lambda x: -(x["label_count"]))

    # 埋点健康：生成率（接收成功占比）与关键字段完整率（标书 F-5-02：99% / 99%）
    ingest_total = conn.execute("SELECT COUNT(*) AS c FROM events WHERE ts>?", (since,)).fetchone()["c"]
    ingest_admitted = conn.execute("SELECT COUNT(*) AS c FROM events WHERE ts>? AND admitted=1", (since,)).fetchone()["c"]
    field_complete = conn.execute(
        "SELECT COUNT(*) AS c FROM events WHERE ts>? AND trace_id!='' AND turn_id!='' AND user_id!='' "
        "AND event_type!=''", (since,)).fetchone()["c"]

    return {
        "window_days": days,
        "ingest_health": {
            "total": ingest_total,
            "admitted": ingest_admitted,
            "admit_rate": round(ingest_admitted / ingest_total, 4) if ingest_total else None,
            "field_complete_rate": round(field_complete / ingest_total, 4) if ingest_total else None,
        },
        "component_trigger_distribution": {k: dict(v) for k, v in trigger_dist.items()},
        "funnel": dict(funnel),
        "funnel_by_type": {k: dict(v) for k, v in funnel_by_type.items()},
        "option_selection_distribution": {k: dict(v) for k, v in option_dist.items()},
        "default_modification": default_stats,
        "group_disagreement": sorted(group_items, key=lambda x: -x["disagreement"])[:10],
        "model_quality": quality_table,
        "labels_by_day": {d: dict(v) for d, v in sorted(label_by_day.items())},
        "labels_total": len(label_rows),
    }


def bank_health(tenant_id: str):
    conn = db.get_conn()
    out = {}
    for layer, cond, args in (("public", "tenant_id IS NULL", ()), ("tenant", "tenant_id=?", (tenant_id,))):
        qs = [dict(r) for r in conn.execute(f"SELECT * FROM bank_queries WHERE {cond}", args).fetchall()]
        qids = [q["query_id"] for q in qs]
        n_resp, sources, last_update = 0, defaultdict(int), None
        if qids:
            ph = ",".join("?" * len(qids))
            for r in conn.execute(f"SELECT label_source, updated_at FROM bank_responses WHERE query_id IN ({ph})", qids).fetchall():
                n_resp += 1
                sources[r["label_source"] or "unknown"] += 1
                if last_update is None or (r["updated_at"] or 0) > last_update:
                    last_update = r["updated_at"]
        domains = defaultdict(int)
        for q in qs:
            for tag in db.dj(q["domain_tags"], []) or ["untagged"]:
                domains[tag] += 1
        out[layer] = {"queries": len(qs), "responses": n_resp, "label_sources": dict(sources),
                      "domains": dict(domains), "last_update": last_update}
    n_tenant = out["tenant"]["responses"]
    out["tenant_weight"] = round(min(0.7, 0.2 + 0.5 * min(1.0, n_tenant / 500.0)), 3) if n_tenant else 0.0
    out["tenant_activation_threshold"] = 500  # TBD-05
    out["tenant_activated"] = n_tenant >= 500
    return out


def ab_compare(days: int = 14):
    """A/B 分组的成本-质量对比（§4.5：策略调整唯一有意义的验收视图）。"""
    conn = db.get_conn()
    since = time.time() - days * 86400
    groups = defaultdict(lambda: {"requests": 0, "cost": 0.0, "latency": [], "quality": [], "aggregated": 0})
    rows = conn.execute("SELECT * FROM traces WHERE ts>? AND ab_group IS NOT NULL", (since,)).fetchall()
    for t in rows:
        g = groups[t["ab_group"]]
        g["requests"] += 1
        g["cost"] += t["total_cost"] or 0
        g["latency"].append(t["total_latency_ms"] or 0)
        if t["switch_result"] == "aggregated":
            g["aggregated"] += 1
    for l in conn.execute(
            "SELECT l.value, t.ab_group FROM labels l JOIN traces t ON l.trace_id=t.trace_id "
            "WHERE l.created_at>? AND t.ab_group IS NOT NULL AND l.label_kind='capability'", (since,)).fetchall():
        groups[l["ab_group"]]["quality"].append(l["value"])
    out = {}
    for name, g in groups.items():
        q = g["quality"]
        out[name] = {
            "requests": g["requests"],
            "avg_cost": round(g["cost"] / g["requests"], 6) if g["requests"] else 0,
            "latency_p95": _pct(g["latency"], 95),
            "aggregation_rate": round(g["aggregated"] / g["requests"], 3) if g["requests"] else 0,
            "avg_quality": round(sum(q) / len(q), 3) if q else None,
            "quality_samples": len(q),
        }
    return out
