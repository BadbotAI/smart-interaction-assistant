"""路由核心（v7 智能路由方案）。

画像 = 公开 benchmark 分数表 + 成本（静态配置，即改即生效）；
每次请求由智能路由模型判定相关 benchmark 维度（可多个），
候选模型取这些维度的平均分 × 成本-效果权重（α）路由；分数接近时聚合定稿。
硬规则（多模态 / 闲聊）前置；智能路由模型未配置时降级为兜底直连（硬依赖）。
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


def get_bench_profile():
    """有效画像分 = 手动修正（kv benchmark_overrides）覆盖榜单快照。返回 (dims, scores, meta)。"""
    conn = db.get_conn()
    row = conn.execute("SELECT v FROM kv_settings WHERE k='benchmark_overrides'").fetchone()
    overrides = db.dj(row["v"], {}) if row else {}
    row2 = conn.execute("SELECT v FROM kv_settings WHERE k='benchmark_asof'").fetchone()
    asof = row2["v"] if row2 else mockmodels.BENCH_SNAPSHOT["asof"]
    scores = {}
    for mid, per in mockmodels.BENCH_SNAPSHOT["scores"].items():
        scores[mid] = dict(per)
    for mid, per in (overrides or {}).items():
        scores.setdefault(mid, {})
        for d, v in per.items():
            scores[mid][d] = v
    return mockmodels.BENCH_DIMS, scores, {"asof": asof, "source": mockmodels.BENCH_SNAPSHOT["source"],
                                            "overrides": overrides}


def get_router_model():
    row = db.get_conn().execute("SELECT v FROM kv_settings WHERE k='router_model_info'").fetchone()
    info = db.dj(row["v"], None) if row else None
    return info if (info and info.get("model_id")) else None


def score_models(models, dims, alpha):
    """按判定维度取平均分（缺失跳过），融合成本：combined = α × perf + (1-α) × 省钱分。"""
    _, scores, _ = get_bench_profile()
    prices = {m["model_id"]: (m["price_input"] or 0) + (m["price_output"] or 0) for m in models}
    inv = {mid: 1.0 / max(0.01, p) for mid, p in prices.items()}
    lo, hi = min(inv.values()), max(inv.values())
    eff = {mid: (v - lo) / (hi - lo) if hi > lo else 0.5 for mid, v in inv.items()}
    out = {}
    for m in models:
        mid = m["model_id"]
        vals = [scores.get(mid, {}).get(d) for d in dims]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue  # 判定维度上全无分数：本次不可选
        perf = sum(vals) / len(vals) / 100.0
        out[mid] = {"perf": round(perf, 3), "eff": round(eff[mid], 3),
                    "combined": round(alpha * perf + (1 - alpha) * eff[mid], 3)}
    return out


async def run_route(req: dict, recorder, emit):
    """执行一次路由。emit(event_dict) 为过程事件回调。返回 final dict。"""
    t_start = time.time()
    query = req["query"]
    tenant_id = req["tenant_id"]
    policy = req["policy"]
    mode = req.get("mode") or "auto"          # auto=智能路由 / manual=指定模型 / multi=多模型+总结
    params = {**DEFAULT_PARAMS, **db.dj(policy.get("params"), {})}
    alpha = float(params.get("alpha", 0.7))
    domain = mockmodels.classify_domain(query)          # 回答内容生成用
    dimension = mockmodels.classify_dimension(query)    # 硬规则判定用（multimodal / chat）
    req["_route"] = {"layer": "dims", "dims": []}

    whitelist = db.dj(policy.get("model_whitelist"), []) or None
    models = get_active_models(whitelist=whitelist)
    if not models:
        return {"error": "no_active_models"}
    by_id = {m["model_id"]: m for m in models}
    default_model = get_default_model(models)
    _fb_id = params.get("fallback_model")
    if _fb_id:
        _fb = next((m for m in get_active_models() if m["model_id"] == _fb_id), None)
        if _fb:
            default_model = _fb

    async def fallback_direct(reason_text, layer):
        req["_route"]["layer"] = layer
        await emit({"step": "degrade" if layer == "else" else "rule", "text": reason_text})
        recorder.span("route_score", {"layer": layer, "fallback": default_model["model_id"]})
        ans = await _call_with_timeout(default_model, query, domain, recorder, emit)
        if ans["status"] != "ok":
            return await _finalize(req, recorder, emit, None, "failed", [ans], {}, {},
                                   [default_model["model_id"]], default_model["model_id"], False, t_start, [],
                                   error="all_models_failed")
        return await _finalize(req, recorder, emit, ans, "fallback", [ans], {}, {},
                               [default_model["model_id"]], default_model["model_id"], False, t_start, [])

    # 手动模式：显式指定模型，不走智能路由；故障切兜底
    if mode == "manual":
        target = by_id.get(req.get("manual_model")) or default_model
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

    # —— 第 1 层 · 硬规则：多模态按能力过滤；闲聊轻量直答 ——
    chat_rule = False
    if dimension == "multimodal":
        req["_route"]["layer"] = "rule"
        mm = [m for m in models if (m["capabilities"] or {}).get("vision")]
        if mm:
            models = mm
            by_id = {m["model_id"]: m for m in models}
            await emit({"step": "rule", "text": f"硬规则命中：多模态请求，仅在 {len(mm)} 个支持图像的模型中路由"})
        else:
            _cap = "" if (default_model["capabilities"] or {}).get("vision")                 else "（兜底模型不具备多模态能力，将按文字尽力回答并说明限制）"
            return await fallback_direct(f"硬规则命中：多模态请求，但候选中没有支持图像的模型，切兜底 {default_model['display_name']}{_cap}", "else")
    elif dimension == "chat":
        chat_rule = True
        req["_route"]["layer"] = "rule"
        await emit({"step": "rule", "text": "硬规则命中：日常闲聊，轻量直答"})

    # —— 智能路由模型（硬依赖）：未配置则不做智能路由，兜底直连 ——
    router_model = get_router_model()
    if not router_model:
        return await fallback_direct(
            f"未配置智能路由模型：无法判定问题相关维度，本次直连兜底 {default_model['display_name']}（请在「模型画像」页配置）",
            "no_router")

    # —— 第 2 层 · 智能判维：路由模型判定相关 benchmark 维度（可多个），取平均分 ——
    t0 = time.time()
    dims = mockmodels.classify_bench_dims(query)
    if dimension == "multimodal" and "multimodal" not in dims:
        dims = ["multimodal"] + dims[:1]
    await asyncio.sleep(0.12 * mockmodels.SIM_SPEED)  # 模拟判维一跳
    judge_ms = int((time.time() - t0) * 1000) + 140
    dim_labels = [next((d["label"] for d in mockmodels.BENCH_DIMS if d["key"] == k), k) for k in dims]
    req["_route"]["layer"] = "rule" if req["_route"]["layer"] == "rule" else "dims"
    req["_route"]["dims"] = dims
    recorder.span("router_judge", {"router_model": router_model["model_id"], "dims": dims}, judge_ms)
    await emit({"step": "dims", "text": f"智能路由模型 {router_model.get('display_name') or router_model['model_id']} 判定："
                                        f"相关维度「{'、'.join(dim_labels)}」（{judge_ms}ms · ¥0.0001）", "dims": dims})

    scored = score_models(models, dims, alpha)
    if not scored:
        return await fallback_direct("候选模型在判定维度上均无画像分数，切兜底直连", "else")
    ranked = sorted(scored.items(), key=lambda x: -x[1]["combined"])
    await emit({"step": "coarse", "text": "取各模型在这些维度的平均分，融合成本得到综合分",
                "scores": {mid: s["combined"] for mid, s in ranked},
                "candidates": [mid for mid, _ in ranked[:2]]})
    recorder.span("route_score", {"dims": dims, "alpha": alpha,
                                  "scores": {mid: s for mid, s in list(scored.items())[:8]}})

    top1_id, top1 = ranked[0]
    gap = top1["combined"] - ranked[1][1]["combined"] if len(ranked) >= 2 else 1.0
    force_agg = bool(policy.get("force_agg"))
    allow_agg = bool(policy.get("allow_aggregation")) or force_agg
    t_val = float(params.get("t", 0.8))
    agg_gap = max(0.02, (1 - t_val) * 0.3)

    do_agg = False
    if mode == "multi":
        do_agg = True
        kept_ids = [mid for mid, _ in ranked]
    elif force_agg and len(ranked) >= 2 and not chat_rule:
        do_agg = True
        kept_ids = [mid for mid, _ in ranked[:2]]
    elif (not chat_rule) and allow_agg and len(ranked) >= 2 and gap <= agg_gap:
        do_agg = True
        kept_ids = [mid for mid, _ in ranked[:2]]

    g = {mid: s["combined"] for mid, s in scored.items()}
    req["_route"]["dim_scores"] = {mid: s for mid, s in scored.items()}

    if not do_agg:
        await emit({"step": "fastlane", "text": f"{by_id[top1_id]['display_name']} 综合分领先，直接作答"})
        ans = await _call_with_timeout(by_id[top1_id], query, domain, recorder, emit)
        if ans["status"] != "ok":
            fb2 = default_model if default_model and default_model["model_id"] != top1_id                 else next((by_id[mid] for mid, _ in ranked[1:2]), None)
            if fb2:
                await emit({"step": "degrade", "text": f"{top1_id} 异常，切换 {fb2['display_name']}"})
                recorder.span("route_switch", {"reason": "primary_failed", "fallback": fb2["model_id"]}, status="degraded")
                ans = await _call_with_timeout(fb2, query, domain, recorder, emit)
        if ans["status"] != "ok":
            return await _finalize(req, recorder, emit, None, "failed", [ans], g, {},
                                   [top1_id], top1_id, False, t_start, [], error="all_models_failed")
        return await _finalize(req, recorder, emit, ans, "fastlane", [ans], g, {},
                               [top1_id], top1_id, False, t_start, [])

    # 聚合：并发作答 → 聚合模型总结定稿
    await emit({"step": "calling", "text": f"综合分接近：{len(kept_ids)} 个候选并发作答",
                "models": [{"id": c, "name": by_id[c]["display_name"]} for c in kept_ids]})
    tasks = [_call_with_timeout(by_id[c], query, domain, recorder, emit) for c in kept_ids]
    answers = [a for a in await asyncio.gather(*tasks) if a["status"] == "ok"]
    if not answers:
        failed_ids = set(kept_ids)
        fb3 = default_model if default_model and default_model["model_id"] not in failed_ids             else next((m for m in get_active_models() if m["model_id"] not in failed_ids), None)
        if not fb3:
            return await _finalize(req, recorder, emit, None, "failed",
                                   [{"model_id": c, "status": "timeout", "content": None, "latency_ms": 0,
                                     "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0, "cost": 0.0}
                                    for c in kept_ids][:1], g, {}, kept_ids, top1_id, False, t_start, [],
                                   error="all_models_failed")
        await emit({"step": "degrade", "text": f"全部候选超时，降级到兜底模型 {fb3['model_id']}"})
        recorder.span("route_switch", {"reason": "all_candidates_failed", "fallback": fb3["model_id"]}, status="degraded")
        ans = await _call_with_timeout(fb3, query, domain, recorder, emit)
        result = "degraded" if ans["status"] == "ok" else "failed"
        return await _finalize(req, recorder, emit, ans if ans["status"] == "ok" else None, result,
                               [ans], g, {}, kept_ids, top1_id, False, t_start, [],
                               error=None if ans["status"] == "ok" else "all_models_failed")
    if len(answers) == 1:
        ans = answers[0]
        return await _finalize(req, recorder, emit, ans, "routed", answers, g, {},
                               kept_ids, top1_id, False, t_start, [])
    aggregator_id = params.get("aggregator_model") if params.get("aggregator_model") in by_id else top1_id
    await emit({"step": "switch", "text": f"保留 {len(answers)} 份回答，交给聚合模型 {by_id[aggregator_id]['display_name']} 总结定稿",
                "result": "aggregated"})
    t0 = time.time()
    agg = mockmodels.aggregate_answers(by_id[aggregator_id], query, domain, answers)
    await asyncio.sleep(agg["latency_ms"] * mockmodels.SIM_SPEED / 1000.0)
    recorder.span("aggregate", {"aggregator": aggregator_id, "input_tokens": agg["tokens_in"],
                                "aggregatees": [a["model_id"] for a in answers]}, int((time.time() - t0) * 1000))
    return await _finalize(req, recorder, emit, agg, "aggregated", answers, g, {},
                           kept_ids, aggregator_id, False, t_start, [], kept=answers)


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
        "route_layer": (req.get("_route") or {}).get("layer"),
        "dimensions": (req.get("_route") or {}).get("dims") or [],
        "dim_scores": (req.get("_route") or {}).get("dim_scores") or {},
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
        # AB 采样候选池：本轮成功返回的各模型完整回答（供数据飞轮出 A/B 双答案）
        "answers": [a for a in all_answers if a and a.get("status") == "ok" and a.get("content")],
    }
