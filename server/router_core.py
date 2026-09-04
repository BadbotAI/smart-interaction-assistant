"""JiSi 路由核心（开发文档 §3）。

在线五步 + 快车道 + 探索预算 + 配额 + 双层 bank（公共底座 / 租户增量）。
超参全部来自 RoutePolicy，论文默认值见 seed.py。
标签衰减：按 created_at 指数衰减，半衰期默认 180 天（TBD-07 临时值）。
"""
import asyncio
import hashlib
import math
import random
import time

from . import db, embeddings, mockmodels

HALF_LIFE_DAYS = 180.0  # TBD-07
MODEL_TIMEOUT_S = 12.0
FAST_GAP = 0.12     # 快车道：top1 与 top2 的 g 差距阈值
FAST_ABS = 0.88     # 快车道：top1 绝对分阈值


def _decay(created_at: float, half_life_days: float = HALF_LIFE_DAYS) -> float:
    age_days = max(0.0, (time.time() - (created_at or time.time())) / 86400.0)
    return 0.5 ** (age_days / half_life_days)


def get_active_models(whitelist=None, require_tool_call=False):
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM models WHERE status='active' AND bank_coverage>=1.0").fetchall()
    models = []
    for r in rows:
        m = dict(r)
        m["capabilities"] = db.dj(m["capabilities"], {})
        m["profile"] = db.dj(m["profile"], {})
        if whitelist and m["model_id"] not in whitelist:
            continue
        if require_tool_call and not m["capabilities"].get("tool_call"):
            continue
        models.append(m)
    return models


def get_default_model(models: list):
    """默认兜底模型：管理员显式指定的故障切换目标（标书 F-5-04：切换成功率 100%）。"""
    for m in models:
        if m.get("is_default"):
            return m
    return models[0] if models else None


def resolve_policy(tenant_id: str, scene: str, session_id: str):
    """就近覆盖：scene > tenant > global；同组 A/B 按 session 哈希分流。"""
    conn = db.get_conn()
    for cond, args in (
        ("scope='scene' AND scene=? AND enabled=1", (scene,)),
        ("scope='tenant' AND tenant_id=? AND enabled=1", (tenant_id,)),
        ("scope='global' AND enabled=1", ()),
    ):
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM policies WHERE {cond}", args).fetchall()]
        if not rows:
            continue
        ab = [r for r in rows if r.get("ab_group")]
        if len(ab) >= 2:
            bucket = int(hashlib.md5((session_id or "").encode()).hexdigest()[:4], 16) % 100
            group_a = next((r for r in ab if r["ab_group"] == "A"), ab[0])
            group_b = next((r for r in ab if r["ab_group"] == "B"), ab[-1])
            return group_a if bucket < group_a.get("ab_split", 50) else group_b
        return rows[0]
    return None


def load_bank(tenant_id: str):
    """加载双层 bank 到内存。演示规模（数百条）下逐请求加载可接受。"""
    conn = db.get_conn()
    queries = conn.execute(
        "SELECT * FROM bank_queries WHERE (tenant_id IS NULL OR tenant_id=?) "
        "AND (source IS NULL OR source != 'reflow_staged')", (tenant_id,)
    ).fetchall()
    qids = [q["query_id"] for q in queries]
    responses = {}
    if qids:
        placeholders = ",".join("?" * len(qids))
        for r in conn.execute(f"SELECT * FROM bank_responses WHERE query_id IN ({placeholders})", qids).fetchall():
            responses.setdefault(r["query_id"], {})[r["model_id"]] = dict(r)
    items = []
    for q in queries:
        items.append({
            "query_id": q["query_id"],
            "tenant_layer": q["tenant_id"] is not None,
            "embedding": db.dj(q["embedding"], []),
            "created_at": q["created_at"],
            "responses": responses.get(q["query_id"], {}),
        })
    return items


def support_set(bank_items, query_emb, n_base: int, gamma: float):
    """Step 1：s_i >= gamma * (第 N_base 高的相似度)。"""
    scored = []
    for it in bank_items:
        s = embeddings.cosine(query_emb, it["embedding"])
        if s > 0:
            scored.append((s, it))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return []
    kth = scored[min(n_base, len(scored)) - 1][0]
    threshold = gamma * kth
    return [(s, it) for s, it in scored if s >= threshold][: n_base * 3]


def coarse_scores(support, model_ids, tenant_weight: float):
    """Step 2：g = v·s，v 为 [0,1] 加权标签（label_value × label_confidence × 时间衰减）。
    双层 bank 分层计算后按 tenant_weight 合并。"""
    def layer_score(layer_items):
        g = {}
        for mid in model_ids:
            num, den = 0.0, 0.0
            for s, it in layer_items:
                resp = it["responses"].get(mid)
                if not resp or resp["label_value"] is None:
                    continue
                eff = resp["label_value"] * (resp["label_confidence"] or 0.5) * _decay(resp["created_at"])
                num += s * eff
                den += s
            g[mid] = num / den if den > 0 else None
        return g

    pub = layer_score([x for x in support if not x[1]["tenant_layer"]])
    ten = layer_score([x for x in support if x[1]["tenant_layer"]])
    merged = {}
    for mid in model_ids:
        gp, gt = pub.get(mid), ten.get(mid)
        if gp is None and gt is None:
            merged[mid] = 0.0
        elif gt is None:
            merged[mid] = gp
        elif gp is None:
            merged[mid] = gt
        else:
            merged[mid] = (1 - tenant_weight) * gp + tenant_weight * gt
    return merged


def tenant_bank_weight(tenant_id: str) -> float:
    """租户标签越多权重越高；启用阈值 500 条（TBD-05 临时值）。"""
    conn = db.get_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM bank_responses br JOIN bank_queries bq ON br.query_id=bq.query_id "
        "WHERE bq.tenant_id=?", (tenant_id,)
    ).fetchone()["c"]
    if n <= 0:
        return 0.0
    return min(0.7, 0.2 + 0.5 * min(1.0, n / 500.0))


def fine_scores(support, answers, eps, sigma, delta, beta, k):
    """Step 4：s_flt = ε·K·s + σ·s_res + δ·s_cost，保留前 β 比例，得 g_f。"""
    g_f = {}
    for ans in answers:
        mid = ans["model_id"]
        resp_emb = embeddings.embed(ans["content"] or "")
        rescored = []
        for s, it in support:
            hist = it["responses"].get(mid)
            if not hist:
                continue
            s_res = embeddings.cosine(resp_emb, db.dj(hist.get("response_embedding"), []))
            c_now, c_hist = ans["tokens_out"] + 1, (hist.get("completion_tokens") or 0) + 1
            s_cost = 1.0 / (1.0 + abs(math.log(c_now / c_hist)))
            s_flt = eps * k * s + sigma * s_res + delta * s_cost
            eff = (hist["label_value"] or 0) * (hist["label_confidence"] or 0.5) * _decay(hist["created_at"])
            rescored.append((s_flt, s, eff))
        if not rescored:
            g_f[mid] = 0.0
            continue
        rescored.sort(key=lambda x: -x[0])
        kept = rescored[: max(1, int(len(rescored) * beta))]
        num = sum(s * eff for _, s, eff in kept)
        den = sum(s for _, s, _ in kept)
        g_f[mid] = num / den if den > 0 else 0.0
    return g_f


def check_quota(tenant_id: str, budget_cap: dict):
    conn = db.get_conn()
    day = time.strftime("%Y-%m-%d")
    row = conn.execute("SELECT * FROM quota_usage WHERE tenant_id=? AND day=?", (tenant_id, day)).fetchone()
    used = row["cost"] if row else 0.0
    cap = (budget_cap or {}).get("daily_usd")
    if cap is not None and used >= cap:
        return False, used, cap
    return True, used, cap


def record_usage(tenant_id: str, tokens: int, cost: float):
    conn = db.get_conn()
    day = time.strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO quota_usage (tenant_id, day, tokens, cost, requests) VALUES (?,?,?,?,1) "
        "ON CONFLICT(tenant_id, day) DO UPDATE SET tokens=tokens+?, cost=cost+?, requests=requests+1",
        (tenant_id, day, tokens, cost, tokens, cost),
    )
    conn.commit()


DEFAULT_PARAMS = {"K": 3, "N_base": 50, "beta": 0.5, "gamma": 0.95,
                  "eps": 0.5, "sigma": 0.3, "delta": 0.2, "t": 0.8, "max_agg_tokens": 13000}


async def run_route(req: dict, recorder, emit):
    """执行一次完整路由。emit(event_dict) 为过程事件回调（SSE / flow.reasoning 数据源）。
    返回 final dict。"""
    t_start = time.time()
    query = req["query"]
    tenant_id = req["tenant_id"]
    policy = req["policy"]
    mode = req.get("mode") or "auto"          # auto=智能路由 / manual=手动选模型 / multi=多模型回答+单模型总结
    params = {**DEFAULT_PARAMS, **db.dj(policy.get("params"), {})}
    K = int(params["K"])
    domain = mockmodels.classify_domain(query)

    whitelist = db.dj(policy.get("model_whitelist"), []) or None
    models = get_active_models(whitelist=whitelist)
    if not models:
        return {"error": "no_active_models"}
    model_ids = [m["model_id"] for m in models]
    by_id = {m["model_id"]: m for m in models}
    default_model = get_default_model(models)

    # 手动模式：管理员/用户显式指定模型，不走路由决策；故障时切默认兜底模型
    if mode == "manual":
        target_id = req.get("manual_model")
        target = by_id.get(target_id) or default_model
        recorder.span("route_score", {"mode": "manual", "target": target["model_id"]})
        await emit({"step": "manual", "text": f"手动指定模型：{target['display_name']}"})
        ans = await _call_with_timeout(target, query, domain, recorder, emit)
        if ans["status"] != "ok" and default_model and default_model["model_id"] != target["model_id"]:
            await emit({"step": "degrade", "text": f"{target['model_id']} 异常，切换默认兜底模型 {default_model['model_id']}"})
            recorder.span("route_switch", {"reason": "manual_target_failed",
                                           "fallback": default_model["model_id"]}, status="degraded")
            ans = await _call_with_timeout(default_model, query, domain, recorder, emit)
        if ans["status"] != "ok":
            return await _finalize(req, recorder, emit, None, "failed", [ans], {}, {},
                                   [target["model_id"]], target["model_id"], False, t_start, [],
                                   error="all_models_failed")
        return await _finalize(req, recorder, emit, ans, "manual", [ans], {}, {},
                               [target["model_id"]], target["model_id"], False, t_start, [])

    # 默认兜底档：不做路由，直接用默认兜底模型回答（不依赖打分成绩，Judge 缺失时依然可用）
    if policy.get("policy_id") == "policy-global-fallback":
        target = default_model or models[0]
        await emit({"step": "fallback", "text": f"默认兜底档：直接调用兜底模型 {target['display_name']}"})
        ans = await _call_with_timeout(target, query, domain, recorder, emit)
        if ans["status"] != "ok":
            return await _finalize(req, recorder, emit, None, "failed", [ans], {}, {},
                                   [target["model_id"]], target["model_id"], False, t_start, [],
                                   error="fallback_model_failed")
        return await _finalize(req, recorder, emit, ans, "fallback", [ans], {}, {},
                               [target["model_id"]], target["model_id"], False, t_start, [])

    # Step 1: embedding + support set
    t0 = time.time()
    q_emb = embeddings.embed(query)
    recorder.span("embed", {"dim": embeddings.DIM}, int((time.time() - t0) * 1000))
    await emit({"step": "embed", "text": "生成问题向量"})

    bank = load_bank(tenant_id)
    support = support_set(bank, q_emb, int(params["N_base"]), float(params["gamma"]))
    await emit({"step": "support", "text": f"命中 {len(support)} 条相似历史问题", "count": len(support)})

    # Step 2: 粗粒度分数
    w_t = tenant_bank_weight(tenant_id)
    g = coarse_scores(support, model_ids, w_t)
    ranked = sorted(g.items(), key=lambda x: -x[1])
    aggregator_id = ranked[0][0] if ranked else model_ids[0]
    # 策略可显式指定聚合器模型（需在线）
    _cfg_agg = params.get("aggregator_model")
    if _cfg_agg and _cfg_agg in g:
        aggregator_id = _cfg_agg
    candidates = [mid for mid, _ in ranked[:K]]
    recorder.span("route_score", {
        "support_count": len(support), "tenant_weight": round(w_t, 3),
        "support_sample": [{"query_id": it["query_id"], "sim": round(s, 4)} for s, it in support[:8]],
        "coarse_scores": {k2: round(v, 4) for k2, v in g.items()},
        "candidates": candidates, "aggregator": aggregator_id, "domain": domain,
    })
    await emit({"step": "coarse", "text": "计算各模型历史命中率",
                "scores": {k2: round(v, 3) for k2, v in ranked}, "candidates": candidates})

    # 探索预算（§3.6）：强制随机打散候选
    is_explore = False
    if random.random() < float(policy.get("explore_ratio") or 0):
        is_explore = True
        candidates = random.sample(model_ids, min(K, len(model_ids)))
        await emit({"step": "explore", "text": "本次为探索流量，随机选择候选模型", "candidates": candidates})

    # 快车道判定（§3.3 落差二）。多模型模式强制并发+聚合，不走快车道
    fastlane = False
    if mode != "multi":
        if policy.get("latency_tier") == "fast" or not policy.get("allow_aggregation"):
            fastlane = True
        elif len(ranked) >= 2 and not is_explore:
            top1, top2 = ranked[0][1], ranked[1][1]
            if (top1 - top2 > FAST_GAP and top1 > 0.5) or top1 > FAST_ABS or domain == "chat":
                fastlane = True
        if not support:
            fastlane = True  # bank 为空时退化为默认单模型

    if fastlane:
        target = candidates[0] if candidates else model_ids[0]
        await emit({"step": "fastlane", "text": f"快车道命中，直接调用 {by_id[target]['display_name']}", "model": target})
        ans = await _call_with_timeout(by_id[target], query, domain, recorder, emit)
        if ans["status"] != "ok":
            fallback = (default_model if default_model and default_model["model_id"] != target
                        else next((m for m in models if m["model_id"] != target), by_id[target]))
            await emit({"step": "degrade", "text": f"{target} 超时，切换默认兜底模型 {fallback['model_id']}"})
            recorder.span("route_switch", {"reason": "primary_timeout", "fallback": fallback["model_id"]}, status="degraded")
            ans = await _call_with_timeout(fallback, query, domain, recorder, emit)
            if ans["status"] != "ok":
                return _finalize(req, recorder, emit, None, "failed", [ans], g, {}, candidates,
                                 aggregator_id, is_explore, t_start, support, error="all_models_failed")
        return await _finalize(req, recorder, emit, ans, "fastlane", [ans], g, {}, candidates,
                               aggregator_id, is_explore, t_start, support)

    # Step 3: 并发调用 K 个候选
    await emit({"step": "calling", "text": f"并发询问 {len(candidates)} 个候选模型",
                "models": [{"id": c, "name": by_id[c]["display_name"]} for c in candidates]})
    tasks = [_call_with_timeout(by_id[c], query, domain, recorder, emit) for c in candidates]
    answers = [a for a in await asyncio.gather(*tasks) if a["status"] == "ok"]
    if not answers:
        fallback = default_model or by_id[candidates[0]]
        recorder.span("route_switch", {"reason": "all_candidates_failed",
                                       "fallback": fallback["model_id"]}, status="degraded")
        await emit({"step": "degrade", "text": f"全部候选超时，降级到默认兜底模型 {fallback['model_id']}"})
        ans = await _call_with_timeout(fallback, query, domain, recorder, emit)
        result = "degraded" if ans["status"] == "ok" else "failed"
        return await _finalize(req, recorder, emit, ans if ans["status"] == "ok" else None, result,
                               [ans], g, {}, candidates, aggregator_id, is_explore, t_start, support,
                               error=None if ans["status"] == "ok" else "all_models_failed")

    # Step 4: 混合相似度细粒度分数
    g_f = fine_scores(support, answers, float(params["eps"]), float(params["sigma"]),
                      float(params["delta"]), float(params["beta"]), K)
    await emit({"step": "fine", "text": "结合回答内容与消耗重估各候选",
                "scores": {k2: round(v, 3) for k2, v in sorted(g_f.items(), key=lambda x: -x[1])}})

    # Step 5: 自适应开关。多模型模式保留全部回答强制聚合（用户显式要求多模型+总结）
    max_gf = max(g_f.values()) if g_f else 0.0
    t_thresh = float(params["t"])
    if mode == "multi":
        kept = list(answers)
    else:
        kept = [a for a in answers if g_f.get(a["model_id"], 0) >= t_thresh * max_gf]
    pruned = [a["model_id"] for a in answers if a not in kept]
    if len(kept) > 1 and sum(a["tokens_out"] for a in kept) > int(params["max_agg_tokens"]):
        kept = sorted(kept, key=lambda a: -g_f.get(a["model_id"], 0))[:2]  # aggregatee 截断
    _max_routes = int(params.get("max_agg_routes") or 0)
    if _max_routes >= 2:
        kept = sorted(kept, key=lambda a: -g_f.get(a["model_id"], 0))[:_max_routes]
    recorder.span("route_switch", {
        "fine_scores": {k2: round(v, 4) for k2, v in g_f.items()},
        "threshold": round(t_thresh * max_gf, 4), "pruned": pruned,
        "result": "routed" if len(kept) == 1 else "aggregated",
    })

    if len(kept) == 1:
        best = kept[0]
        await emit({"step": "switch", "text": f"细粒度评估后单模型胜出：{by_id[best['model_id']]['display_name']}，剪掉 {len(pruned)} 个",
                    "result": "routed", "pruned": pruned})
        return await _finalize(req, recorder, emit, best, "routed", answers, g, g_f, candidates,
                               aggregator_id, is_explore, t_start, support)

    # 聚合
    await emit({"step": "switch", "text": f"保留 {len(kept)} 份回答，交给聚合器 {by_id[aggregator_id]['display_name']} 融合重写",
                "result": "aggregated", "pruned": pruned})
    t0 = time.time()
    agg = mockmodels.aggregate_answers(by_id[aggregator_id], query, domain, kept)
    await asyncio.sleep(agg["latency_ms"] * mockmodels.SIM_SPEED / 1000.0)
    recorder.span("aggregate", {
        "aggregator": aggregator_id, "input_tokens": agg["tokens_in"],
        "aggregatees": [a["model_id"] for a in kept],
        "truncated": len(kept) < len([a for a in answers if g_f.get(a["model_id"], 0) >= t_thresh * max_gf]),
    }, int((time.time() - t0) * 1000))
    return await _finalize(req, recorder, emit, agg, "aggregated", answers, g, g_f, candidates,
                           aggregator_id, is_explore, t_start, support, kept=kept)


async def _call_with_timeout(model: dict, query: str, domain: str, recorder, emit):
    t0 = time.time()
    try:
        ans = await asyncio.wait_for(
            mockmodels.call_model(model, query, domain, MODEL_TIMEOUT_S), MODEL_TIMEOUT_S)
    except asyncio.TimeoutError:
        ans = {"model_id": model["model_id"], "status": "timeout", "content": None, "data": None,
               "latency_ms": int(MODEL_TIMEOUT_S * 1000), "tokens_in": 0, "tokens_out": 0,
               "tokens_thinking": 0, "cost": 0.0, "correct": False}
    recorder.span("model_call", {
        "model_id": ans["model_id"], "latency_ms": ans["latency_ms"],
        "tokens_in": ans["tokens_in"], "tokens_out": ans["tokens_out"],
        "tokens_thinking": ans["tokens_thinking"], "cost": ans["cost"],
    }, int((time.time() - t0) * 1000), status=ans["status"])
    await emit({"step": "model_done", "model": ans["model_id"], "status": ans["status"],
                "latency_ms": ans["latency_ms"],
                "text": f"{model['display_name']} {'已返回' if ans['status'] == 'ok' else '超时'}"})
    return ans


async def _finalize(req, recorder, emit, final_ans, switch_result, all_answers, g, g_f,
                    candidates, aggregator_id, is_explore, t_start, support,
                    kept=None, error=None):
    total_latency = int((time.time() - t_start) * 1000)
    calls = [a for a in all_answers if a] + ([] if (final_ans in all_answers or final_ans is None) else [final_ans])
    total_cost = round(sum(a["cost"] for a in calls), 8)
    total_tokens = sum(a["tokens_in"] + a["tokens_out"] + a["tokens_thinking"] for a in calls)
    record_usage(req["tenant_id"], total_tokens, total_cost)

    decision = {
        "trace_id": recorder.trace_id, "tenant_id": req["tenant_id"],
        "mode": req.get("mode") or "auto",
        "policy_id": req["policy"]["policy_id"], "policy_version": req["policy"]["version"],
        "support_set_ids": [it["query_id"] for _, it in support[:50]],
        "coarse_scores": {k: round(v, 4) for k, v in g.items()},
        "candidate_models": candidates, "aggregator_model": aggregator_id,
        "model_calls": [{"model_id": a["model_id"], "latency_ms": a["latency_ms"],
                         "tokens_in": a["tokens_in"], "tokens_out": a["tokens_out"],
                         "tokens_thinking": a["tokens_thinking"], "cost": a["cost"],
                         "status": a["status"],
                         "resp_emb": embeddings.embed(a["content"]) if a.get("content") else None} for a in calls],
        "fine_scores": {k: round(v, 4) for k, v in (g_f or {}).items()},
        "switch_result": switch_result,
        "final_model_or_aggregator": final_ans["model_id"] if final_ans else None,
        "is_explore": is_explore, "total_cost": total_cost, "total_latency_ms": total_latency,
    }
    conn = db.get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO route_decisions (trace_id, tenant_id, policy_id, policy_version, decision) VALUES (?,?,?,?,?)",
        (recorder.trace_id, req["tenant_id"], req["policy"]["policy_id"], req["policy"]["version"], db.j(decision)))
    conn.commit()
    recorder.finish(switch_result, decision["final_model_or_aggregator"], total_cost, total_latency,
                    is_explore, status="ok" if not error else "error")
    return {
        "decision": decision, "final": final_ans, "error": error,
        "aggregation_candidates": kept if switch_result == "aggregated" else None,
    }
