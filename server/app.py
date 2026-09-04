"""智能助手交互与调度平台 — 服务端入口。

运行：uvicorn server.app:app --reload --port 8787
首次启动自动建库并注入种子数据。
"""
import asyncio
import json
import os
import random
import time

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import cards, dashboard, db, embeddings, events, mockmodels, router_core, seed, traces

app = FastAPI(title="智能助手交互与调度平台", version="0.1.0")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@app.on_event("startup")
def startup():
    seed.run_all()


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """演示环境：前端静态资源强制走服务端校验，避免改版后浏览器用旧缓存。"""
    response = await call_next(request)
    if request.url.path.startswith(("/web", "/brand", "/contracts")) or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache"
    return response


# ============ P2 路由服务 ============

@app.post("/v1/route")
async def route(request: Request):
    """对话主入口。SSE 流：先推执行过程事件（供 flow.reasoning 渲染），最终内容就绪后推 final。"""
    body = await request.json()
    return StreamingResponse(_route_stream(body), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _route_stream(body: dict):
    queue = asyncio.Queue()

    async def emit(evt):
        await queue.put(evt)

    task = asyncio.create_task(_handle_turn(body, emit))
    while True:
        get = asyncio.create_task(queue.get())
        done, _ = await asyncio.wait({get, task}, return_when=asyncio.FIRST_COMPLETED)
        if get in done:
            evt = get.result()
            yield f"data: {db.j(evt)}\n\n"
            if evt.get("step") == "final":
                break
        else:
            get.cancel()
            while not queue.empty():
                evt = queue.get_nowait()
                yield f"data: {db.j(evt)}\n\n"
                if evt.get("step") == "final":
                    return
            exc = task.exception()
            yield f"data: {db.j({'step': 'final', 'error': str(exc) if exc else 'internal', 'content': '系统繁忙，请稍后重试。'})}\n\n"
            return
    if not task.done():
        await task


def _envelope(component_type, trigger_source, params, card=None, group_mode=None, degraded_text=""):
    return {
        "schema_version": "1.0.0", "render_id": db.new_id(),
        "component_type": component_type,
        "semantic_category": cards.semantic_category(component_type),
        "trigger_source": trigger_source,
        "card_ref": {"card_id": card["card_id"], "version": card["version"]} if card else None,
        "params": params, "group_mode": group_mode,
        "degraded_text": degraded_text or params.get("prompt") or "",
    }


async def _handle_turn(body: dict, emit):
    tenant_id = body.get("tenant_id") or seed.TENANT
    session_id = body.get("session_id") or "sess-default"
    user_id = body.get("user_id") or "user-demo"
    scene = body.get("scene")
    text = (body.get("text") or "").strip()
    card_context = body.get("card_context")
    turn_id = db.new_id()
    trace_id = db.new_id()

    policy = router_core.resolve_policy(tenant_id, scene, session_id)
    # 对外接入：api_key 即策略凭证，用户产品带 Key 调用即绑定对应策略（无需感知策略 ID）
    if body.get("api_key"):
        _touch_api_key(body["api_key"])
        for _r in db.get_conn().execute("SELECT policy_id FROM policies WHERE enabled=1").fetchall():
            if _policy_api_key(_r["policy_id"]) == body["api_key"]:
                body["policy_id"] = _r["policy_id"]
                break
    # 测试抽屉可显式指定调度策略（仅限已启用策略）
    if body.get("policy_id"):
        _row = db.get_conn().execute("SELECT * FROM policies WHERE policy_id=? AND enabled=1",
                                     (body["policy_id"],)).fetchone()
        if _row:
            policy = dict(_row)
    if not policy:
        await emit({"step": "final", "error": "no_policy", "content": "未配置可用路由策略，请联系管理员。"})
        return

    recorder = traces.TraceRecorder(trace_id, tenant_id, session_id, turn_id, user_id,
                                    text, policy["policy_id"], policy.get("ab_group"))
    recorder.span("user_input", {"query": text, "scene": scene,
                                 "card_context": bool(card_context)})

    # 配额检查（§3.7）：超限默认降级到单模型
    ok, used, cap = router_core.check_quota(tenant_id, db.dj(policy.get("budget_cap"), {}))
    degrade_by_quota = False
    if not ok:
        degrade_by_quota = True
        await emit({"step": "quota", "text": f"今日成本 {used:.4f} 美元已达配额上限 {cap} 美元，本次降级为单模型直连"})

    # 采集/控制卡片触发（模型自主 tool call 的模拟；已带 card_context 的续轮不再触发）
    if not card_context and not body.get("skip_card_match"):
        hit, competitors = cards.match_cards(text, tenant_id)
        if hit:
            envelope, degraded = _build_ask_envelope(hit, text)
            recorder.span("card_render", {
                "card_id": hit["card_id"], "card_version": hit["version"],
                "component_type": hit["component_type"], "trigger_source": "model_tool_call",
                "degraded": bool(degraded), "competitors": competitors[:3],
            }, status="degraded" if degraded else "ok")
            recorder.finish("await_user", None, 0.0, 0, False)
            # 场景配置了信息内容（reply_text）时先展示信息，再出交互组件
            await emit({"step": "final", "trace_id": trace_id, "turn_id": turn_id,
                        "content": envelope["params"].get("reply_text")
                                   or envelope["params"].get("prompt", "请补充信息"),
                        "ask_card": envelope, "await_user": True})
            return

    # 带着卡片回流数据续轮：把用户选择并入 query；选项配置了后续动作时按动作走
    route_text = text
    if card_context:
        summary = card_context.get("summary") or ""
        route_text = f"{text}（用户通过卡片提交：{summary}）" if summary else text
        # 需求：不同选项 → 对应的服务或回复。查配置里该选项的 prompt / 服务接口
        act_card_id, selection = card_context.get("card_id"), card_context.get("selection")
        if act_card_id and selection is not None:
            act_card = cards.get_card(act_card_id)
            actions = ((act_card or {}).get("field_bindings") or {}).get("config", {}).get("option_actions") or {}
            # 多选：每个选中项的动作都执行——跳转 / 服务链接逐个触发，AI 跟进合并成一段
            sels = selection if isinstance(selection, list) else [selection]
            prompts = []
            for sel in sels:
                act = actions.get(str(sel)) or {}
                if act.get("api"):
                    await emit({"step": "tool", "text": f"按选项「{sel}」调用服务接口 {act['api']}（演示模拟，未真实外发）"})
                    recorder.span("tool_call", {"card_id": act_card_id, "option": sel, "endpoint": act["api"], "mocked": True})
                if act.get("prompt"):
                    prompts.append(f"「{sel}」：{act['prompt']}" if len(sels) > 1 else act["prompt"])
            if prompts:
                route_text = "；".join(prompts) + f"（用户选择：{'、'.join(str(x) for x in sels)}）"

    # 三种调用模式（标书 F-5-04）：auto=智能路由 / manual=手动选模型 / multi=多模型回答+单模型总结
    mode = body.get("mode") or "auto"
    req = {"query": route_text, "tenant_id": tenant_id, "policy": dict(policy),
           "mode": mode, "manual_model": body.get("manual_model")}
    if mode == "multi":
        req["policy"]["allow_aggregation"] = 1
        req["policy"]["explore_ratio"] = 0
    if degrade_by_quota:
        req["policy"]["latency_tier"] = "fast"
        req["policy"]["allow_aggregation"] = 0
        req["policy"]["explore_ratio"] = 0
        req["mode"] = "auto" if mode == "multi" else req["mode"]

    result = await router_core.run_route(req, recorder, emit)
    if result.get("error") and not result.get("final"):
        await emit({"step": "final", "trace_id": trace_id, "error": result["error"],
                    "content": "所有候选模型均不可用，请稍后重试。"})
        return

    final = result["final"]
    decision = result["decision"]

    # （已按产品决策停用对话 query 自动回流采集：场景数据集以导入为准）

    components = []

    # 呈现型：模型显式返回结构化数据 → 按映射表渲染；失败降级纯文本（§2.5 降级链）
    if final.get("data"):
        comp = _envelope(final["data"]["kind"], "model_tool_call", final["data"]["params"],
                         degraded_text=final["content"])
        components.append(comp)
        recorder.span("card_render", {"component_type": comp["component_type"],
                                      "trigger_source": "model_tool_call", "render_id": comp["render_id"]})

    # 评价型：系统固定注入（§2.5）。feedback.binary 必须区分能力/偏好两个维度（§2.3.4）
    preset = _load_preset()
    fb_dims = preset.get("component_defaults", {}).get("feedback.binary", {}).get("dimensions") or [
        {"key": "capability", "label": "答得准确吗"}, {"key": "preference", "label": "合你的需要吗"}]
    components.append(_envelope("feedback.binary", "system_injected",
                                {"dimensions": fb_dims, "target_models": [final["model_id"]]}))

    # 聚合路径：注入多回答择优（pairwise 最高质量标签源）
    if result.get("aggregation_candidates"):
        cands = [{"model_id": a["model_id"], "alias": f"候选{i + 1}", "content": a["content"]}
                 for i, a in enumerate(result["aggregation_candidates"])]
        components.append(_envelope("feedback.preference", "system_injected", {"candidates": cands}))

    await emit({
        "step": "final", "trace_id": trace_id, "turn_id": turn_id,
        "content": final["content"], "components": components,
        "decision_summary": {
            "mode": decision.get("mode", "auto"),
            "switch_result": decision["switch_result"],
            "final_model": decision["final_model_or_aggregator"],
            "candidates": decision["candidate_models"],
            "aggregator": decision.get("aggregator_model"),
            "is_explore": decision["is_explore"],
            "total_cost": decision["total_cost"],
            "total_latency_ms": decision["total_latency_ms"],
            "model_calls": decision.get("model_calls") or [],
            "policy": {
                "policy_id": policy.get("policy_id"),
                "name": policy.get("name"),
                "latency_tier": policy.get("latency_tier"),
                "explore_ratio": policy.get("explore_ratio"),
                "allow_aggregation": policy.get("allow_aggregation"),
                "K": (db.dj(policy.get("params"), {}) if isinstance(policy.get("params"), str)
                      else (policy.get("params") or {})).get("K"),
            },
        },
        "usage": {"cost": decision["total_cost"],
                  "tokens": sum(c["tokens_in"] + c["tokens_out"] + c.get("tokens_thinking", 0)
                                for c in decision["model_calls"])},
        "route_context": {"policy_id": policy["policy_id"],
                          "selected_models": decision["candidate_models"],
                          "aggregator": decision["aggregator_model"],
                          "switch_result": decision["switch_result"],
                          "is_explore": decision["is_explore"]},
    })


@app.get("/v1/embed/envelope/{card_id}")
def embed_envelope(card_id: str):
    """植入 SDK：按配置 ID 获取可渲染的协议信封（sia.js 用）。仅已上线配置可被植入。"""
    card = cards.get_card(card_id)
    if not card:
        return JSONResponse({"error": "配置不存在"}, status_code=404)
    if not (card.get("status") == "published" or (card.get("status") == "draft" and (card.get("version") or 0) >= 1)):
        return JSONResponse({"error": "配置未上线，不能植入"}, status_code=409)
    envelope, degraded = _build_ask_envelope(card, "")
    return {"envelope": envelope, "degraded": bool(degraded),
            "card": {"card_id": card["card_id"], "name": card["name"], "version": card.get("version")}}


def _build_ask_envelope(card: dict, query: str):
    """把命中的采集/控制卡片实例化为组件信封。
    优先使用管理端编辑的模版配置（field_bindings.config）；API 选项源失败 → 空态降级（§2.8）。"""
    ct = card["component_type"]
    templates = card.get("text_templates") or {}
    config = (card.get("field_bindings") or {}).get("config") or {}
    degraded = None
    params = {"prompt": templates.get("prompt") or f"请补充「{card['name']}」相关信息",
              "submit_label": templates.get("submit") or "提交",
              "reply_text": templates.get("reply") or "",
              "echo_results": bool(card.get("echo_results"))}
    if config.get("steps"):
        params["steps"] = config["steps"]
    if config.get("display"):
        params["display"] = config["display"]  # 显示样式变体
    if config.get("option_meta"):
        params["option_meta"] = config["option_meta"]  # 卡片样式的描述 / 配图

    def get_options():
        if config.get("options"):
            return list(config["options"]), None
        return cards.resolve_options(card, query)

    if ct in ("select.single", "select.card", "select.multi", "commerce.order"):
        options, degraded = get_options()
        if degraded:
            params["empty_state"] = f"选项暂时无法加载：{degraded}。你可以直接文字描述你的选择。"
            params["options"] = []
        else:
            params["options"] = options
            params["recommended_default"] = config.get("recommended_default") or (options[0] if options else None)
        # 选项后续动作（每个选项可配置提交后的 prompt 与服务接口）随信封下发，前端提交时回传
        if config.get("option_actions"):
            params["option_actions"] = config["option_actions"]
    elif ct == "scale.likert":
        params["likert"] = config.get("likert") or {"left": "非常不认可", "right": "非常认可", "steps": 5}
    elif ct == "slider.range":
        slider = config.get("slider") or {}
        params["min"] = slider.get("min", 0)
        params["max"] = slider.get("max", 100)
        params["unit"] = slider.get("unit", "")
    elif ct == "matrix.compare+select":
        options, degraded = get_options()
        preset = _load_preset()
        dims = (config.get("dimensions")
                or preset.get("component_defaults", {}).get("matrix.compare", {}).get("dimensions")
                or ["价格", "时效", "风险", "合规"])
        if degraded or not options:
            params["empty_state"] = f"选项暂时无法加载：{degraded or '无可用选项'}"
            params["options"], params["dimensions"], params["values"] = [], dims, []
        else:
            rng = random.Random(hash(query) & 0xFFFF)
            params["options"] = options
            params["dimensions"] = dims
            params["values"] = config.get("values") or [[round(rng.random() * 4 + 5, 1) for _ in dims] for _ in options]
            best = max(range(len(options)), key=lambda i: sum(params["values"][i]))
            params["recommended_default"] = config.get("recommended_default") or options[best]
    elif ct == "form.structured":
        params["fields"] = config.get("fields") or (card.get("field_bindings") or {}).get("fields") or []
    elif ct == "input.followup":
        params["placeholder"] = config.get("placeholder") or "请输入你的回答"
    elif ct == "rank.priority":
        options, degraded = get_options()
        params["options"] = options or []
    elif ct == "picker.location":
        options, _ = get_options()
        params["options"] = options or []
        params["placeholder"] = config.get("placeholder") or ""
    elif ct in ("picker.datetime", "picker.timerange"):
        pass  # 仅 display 变体
    elif ct in ("upload.file", "upload.image"):
        params["placeholder"] = config.get("placeholder") or ""
    elif ct in ("suggest.followup", "entry.link"):
        options, _ = get_options()
        params["options"] = options or []
        if config.get("option_actions"):
            params["option_actions"] = config["option_actions"]
    elif ct == "control.confirm":
        params["action_desc"] = f"检测到高风险操作意图：「{query}」"
        params["risk_level"] = "high"
        params["title"] = templates.get("title") or "操作确认"
        params["confirm_label"] = templates.get("confirm") or "确认执行"
        params["cancel_label"] = templates.get("cancel") or "取消"
    return _envelope(ct, "model_tool_call", params, card=card, group_mode=None), degraded


def _load_preset():
    path = os.path.join(BASE, "brand", "industry-preset.supplychain.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {}


@app.get("/v1/route/{trace_id}/explain")
def explain(trace_id: str):
    conn = db.get_conn()
    row = conn.execute("SELECT decision FROM route_decisions WHERE trace_id=?", (trace_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    decision = db.dj(row["decision"], {})
    support = []
    ids = decision.get("support_set_ids", [])[:10]
    if ids:
        ph = ",".join("?" * len(ids))
        for q in conn.execute(f"SELECT query_id, query_text, domain_tags FROM bank_queries WHERE query_id IN ({ph})", ids):
            hits = {}
            for r in conn.execute("SELECT model_id, label_value FROM bank_responses WHERE query_id=?", (q["query_id"],)):
                hits[r["model_id"]] = r["label_value"]
            support.append({"query_id": q["query_id"], "query_text": traces.mask_text(q["query_text"]),
                            "domains": db.dj(q["domain_tags"], []), "model_hits": hits})
    decision.pop("support_set_ids", None)
    for c in decision.get("model_calls", []):
        c.pop("resp_emb", None)
    return {"support_set": support, "coarse_scores": decision.get("coarse_scores"),
            "fine_scores": decision.get("fine_scores"), "switch_result": decision.get("switch_result"),
            "decision": decision}


# ============ 事件接入（P1 回流 SDK 服务端） ============

@app.post("/v1/events")
async def ingest_events(request: Request):
    body = await request.json()
    evts = body.get("events") if isinstance(body, dict) and "events" in body else [body]
    results = [events.ingest(e) for e in evts]
    return {"results": results}


# ============ 群体决策（服务端串行化，§2.4） ============

@app.post("/v1/group/vote")
async def group_vote(request: Request):
    body = await request.json()
    render_id, participant = body.get("render_id"), body.get("participant")
    card = cards.get_card(body.get("card_id")) if body.get("card_id") else None
    gm = (card or {}).get("group_mode") or {"visibility": "realtime", "revisable": True,
                                            "aggregation_rule": "majority", "deadline": {"quorum": 3}}
    conn = db.get_conn()
    existing = conn.execute("SELECT * FROM group_votes WHERE render_id=? AND participant=?",
                            (render_id, participant)).fetchone()
    if existing and not gm.get("revisable", True):
        return JSONResponse({"error": "vote_not_revisable", "message": "该卡片配置为投票后不可修改"}, status_code=409)
    conn.execute("INSERT OR REPLACE INTO group_votes (render_id, trace_id, participant, choice, ts) VALUES (?,?,?,?,?)",
                 (render_id, body.get("trace_id"), participant, body.get("choice"), db.now_ts()))
    conn.commit()
    return _group_state(render_id, gm, requester=participant)


@app.get("/v1/group/{render_id}/state")
def group_state(render_id: str, card_id: str = None, participant: str = None):
    card = cards.get_card(card_id) if card_id else None
    gm = (card or {}).get("group_mode") or {"visibility": "realtime", "aggregation_rule": "majority",
                                            "deadline": {"quorum": 3}}
    return _group_state(render_id, gm, requester=participant)


def _group_state(render_id: str, gm: dict, requester: str = None):
    conn = db.get_conn()
    votes = [dict(r) for r in conn.execute("SELECT * FROM group_votes WHERE render_id=?", (render_id,)).fetchall()]
    quorum = (gm.get("deadline") or {}).get("quorum", 3)
    closed = len(votes) >= quorum
    dist = {}
    for v in votes:
        dist[v["choice"]] = dist.get(v["choice"], 0) + 1
    final = max(dist, key=dist.get) if dist and closed else None
    sealed = gm.get("visibility") == "sealed" and not closed
    # sealed 模式：未揭晓前任何客户端不得取到他人选择
    visible_dist = None if sealed else dist
    own = next((v["choice"] for v in votes if v["participant"] == requester), None)
    return {"render_id": render_id, "votes_count": len(votes), "quorum": quorum, "closed": closed,
            "sealed_pending": sealed, "distribution": visible_dist, "final": final,
            "own_choice": own, "aggregation_rule": gm.get("aggregation_rule", "majority"),
            "feedback_to_model": gm.get("feedback_to_model", "distribution")}


# ============ P1 卡片管理 API ============

@app.get("/api/component-types")
def component_types():
    return {
        "present": sorted(cards.PRESENT_TYPES), "collect": sorted(cards.COLLECT_TYPES),
        "control": sorted(cards.CONTROL_TYPES), "evaluate": sorted(cards.EVALUATE_TYPES),
    }


@app.get("/api/cards")
def list_cards(status: str = None, q: str = None, tenant_id: str = None):
    conn = db.get_conn()
    where, args = ["tenant_id=?"], [tenant_id or seed.TENANT]
    if status:
        where.append("status=?"); args.append(status)
    else:
        where.append("status!='deleted'")
    if q:
        # 走查：搜索覆盖名称 / 触发条件 / 示例问法 / 提问文案；用户输入的 % _ 按字面匹配
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' OR trigger_description LIKE ? ESCAPE '\\' "
                     "OR trigger_examples LIKE ? ESCAPE '\\' OR json_extract(text_templates,'$.prompt') LIKE ? ESCAPE '\\')")
        args.extend([f"%{esc}%"] * 5)
    rows = conn.execute(f"SELECT * FROM cards WHERE {' AND '.join(where)} ORDER BY updated_at DESC", args).fetchall()
    out = []
    for r in rows:
        card = cards.row_to_card(r)
        refs = conn.execute("SELECT agent_id, version FROM card_refs WHERE card_id=?", (card["card_id"],)).fetchall()
        card["referenced_by"] = [dict(x) for x in refs]
        card["stale_refs"] = sum(1 for x in refs if x["version"] < card["version"])
        out.append(card)
    return {"cards": out}


@app.post("/api/cards")
async def create_card(request: Request):
    body = await request.json()
    card, errors = cards.create_card(body.get("tenant_id") or seed.TENANT, body)
    if errors:
        return JSONResponse({"errors": errors}, status_code=422)
    return {"card": card}


@app.get("/api/cards/{card_id}")
def get_card_detail(card_id: str):
    card = cards.get_card(card_id)
    if not card:
        return JSONResponse({"error": "not_found"}, status_code=404)
    conn = db.get_conn()
    snaps = [{"version": r["version"], "published_at": r["published_at"], "archived": bool(r["archived"])}
             for r in conn.execute("SELECT version, published_at, archived FROM card_snapshots "
                                   "WHERE card_id=? ORDER BY version DESC", (card_id,)).fetchall()]
    refs = [dict(r) for r in conn.execute("SELECT agent_id, version FROM card_refs WHERE card_id=?", (card_id,)).fetchall()]
    card["snapshots"] = snaps
    card["referenced_by"] = refs
    return {"card": card}


@app.put("/api/cards/{card_id}")
async def update_card(card_id: str, request: Request):
    body = await request.json()
    card, errors = cards.update_card(card_id, body.get("payload") or {}, int(body.get("lock_version", -1)))
    if errors:
        code = 409 if any(e.get("code") in ("conflict", "card_deleted") for e in errors) else 422
        return JSONResponse({"errors": errors}, status_code=code)
    return {"card": card}


@app.post("/api/cards/{card_id}/transition")
async def card_transition(card_id: str, request: Request):
    body = await request.json()
    action = body.get("action")
    force = body.get("version") if action in ("rollback", "restore_draft") else bool(body.get("force"))
    card, err = cards.transition(card_id, action, actor=body.get("actor", "demo-admin"), force=force)
    warning = None
    if action == "publish" and card and not err:
        q = (card.get("trigger_examples") or [None])[0] or card.get("trigger_description") or ""
        if q:
            _, competitors = cards.match_cards(q, card.get("tenant_id") or seed.TENANT)
            rival = next((c for c in competitors if c["card_id"] != card_id and c["score"] >= 0.5), None)
            if rival:
                warning = f"注意：触发条件与已上线配置「{rival['name']}」相似度较高，可能出现抢触发，建议在测试抽屉验证"
    if err:
        return JSONResponse({"error": err}, status_code=409)
    return {"card": card, "warning": warning}


@app.post("/api/cards/{card_id}/upgrade-refs")
def upgrade_refs(card_id: str):
    """批量把引用旧版本的 Agent 升级到当前版本（§2.7）。"""
    card = cards.get_card(card_id)
    if not card:
        return JSONResponse({"error": "not_found"}, status_code=404)
    conn = db.get_conn()
    n = conn.execute("UPDATE card_refs SET version=? WHERE card_id=?", (card["version"], card_id)).rowcount
    conn.commit()
    db.audit("demo-admin", "card_refs_upgrade", {"card_id": card_id, "to_version": card["version"], "count": n})
    return {"upgraded": n, "version": card["version"]}


@app.post("/api/cards/debug")
async def debug_card(request: Request):
    """trigger_description 调试闭环（§2.6）：输入问法 → 命中判定 + 参数填充 + 竞争卡片。"""
    body = await request.json()
    query = body.get("query") or ""
    hit, competitors = cards.match_cards(query, body.get("tenant_id") or seed.TENANT)
    result = {"query": query, "competitors": competitors, "hit": None}
    if hit:
        result["hit"] = {"card_id": hit["card_id"], "name": hit["name"],
                         "component_type": hit["component_type"],
                         "filled_params": cards.fill_debug_params(hit, query)}
    return result


# ============ 信息模版：库 + AI 匹配 + 群体回显聚合 ============

@app.get("/api/templates")
def list_templates():
    return {"templates": [{k: t[k] for k in ("component_type", "name", "desc", "default_config")}
                          for t in cards.TEMPLATE_LIBRARY]}


@app.post("/api/templates/suggest")
async def suggest_templates(request: Request):
    """输入问题/场景描述 → AI 自动匹配合适的信息模版。"""
    body = await request.json()
    question = (body.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "问题不能为空"}, status_code=422)
    return {"question": question, "suggestions": cards.suggest_templates(question)}


@app.post("/api/scenarios/rewrite-trigger")
async def rewrite_trigger(request: Request):
    """AI 改写触发条件描述，并生成示例问法。"""
    body = await request.json()
    desc = (body.get("description") or "").strip()
    if not desc:
        return JSONResponse({"error": "描述不能为空"}, status_code=422)
    return cards.rewrite_trigger(desc)


@app.get("/v1/cards/{card_id}/responses")
def card_responses(card_id: str):
    """群体回显数据：该问题所有已提交回答的聚合分布。
    卡片开启 echo_results 后，用户提交完成即可看到其他人的选择情况。"""
    card = cards.get_card(card_id)
    if not card:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if not card.get("echo_results"):
        # 回显开关在服务端生效：关闭即任何端都取不到分布
        return JSONResponse({"error": "echo_disabled", "message": "该配置未开启回显"}, status_code=403)
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT payload, user_id, ts FROM events WHERE event_type='card_submitted' AND admitted=1 "
        "AND COALESCE(channel,'')!='test' "
        "AND json_extract(card,'$.card_id')=? ORDER BY ts DESC", (card_id,)).fetchall()
    cfg_now = ((card.get("field_bindings") or {}).get("config") or {})
    distribution = {}
    recent_texts = []
    respondents = set()
    for r in rows:
        p = db.dj(r["payload"], {})
        respondents.add(r["user_id"])
        sel = p.get("user_selection")
        if sel is None:
            continue
        if isinstance(sel, list):
            for item in sel:
                k = cards.resolve_option_alias(cfg_now, str(item))
                distribution[k] = distribution.get(k, 0) + 1
        elif isinstance(sel, str) and len(sel) > 24 and len(recent_texts) < 8:
            recent_texts.append(sel[:80] + ("…" if len(sel) > 80 else ""))  # 面向用户端只给截断摘要
        else:
            k = cards.resolve_option_alias(cfg_now, str(sel))
            distribution[k] = distribution.get(k, 0) + 1
    return {"card_id": card_id, "total_submissions": len(rows),
            "respondents": len(respondents),
            "distribution": dict(sorted(distribution.items(), key=lambda x: -x[1])),
            "recent_texts": recent_texts}


@app.post("/api/cards/{card_id}/echo")
async def toggle_echo(card_id: str, request: Request):
    """运营侧回显开关（标书 F-5-05：支持群体决策回显的运营配置）。
    回显属于运行时运营配置而非内容快照的一部分：同时更新卡片行与当前生效快照，即时生效。"""
    body = await request.json()
    enabled = 1 if body.get("enabled") else 0
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM cards WHERE card_id=?", (card_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    conn.execute("UPDATE cards SET echo_results=? WHERE card_id=?", (enabled, card_id))
    snap = conn.execute("SELECT version, snapshot FROM card_snapshots WHERE card_id=? AND archived=0",
                        (card_id,)).fetchone()
    if snap:
        s = db.dj(snap["snapshot"], {})
        s["echo_results"] = bool(enabled)
        conn.execute("UPDATE card_snapshots SET snapshot=? WHERE card_id=? AND version=?",
                     (db.j(s), card_id, snap["version"]))
    conn.commit()
    db.audit("demo-admin", "echo_toggle", {"card_id": card_id, "enabled": bool(enabled)})
    return {"ok": True, "echo_results": bool(enabled)}


@app.get("/api/dashboard/questions")
def dashboard_questions(days: int = Query(30, ge=1, le=90)):
    """工具1（问卷与交互）视角：每个问题的参与人数、选项占比、完成漏斗。"""
    import time as _t
    conn = db.get_conn()
    since = _t.time() - days * 86400
    # 有生效快照的编辑中草稿（status=draft 且 version>=1）线上仍在服务，同样计入
    card_rows = conn.execute(
        "SELECT * FROM cards WHERE (status IN ('published','offline') OR (status='draft' AND version>=1)) "
        "AND semantic_category='collect'").fetchall()
    out = []
    for c in card_rows:
        card = cards.row_to_card(c)
        stats = {"rendered": 0, "started": 0, "submitted": 0, "respondents": set()}
        distribution = {}
        for r in conn.execute(
                "SELECT event_type, payload, user_id FROM events WHERE json_extract(card,'$.card_id')=? "
                "AND admitted=1 AND COALESCE(channel,'')!='test' AND ts>?", (card["card_id"], since)).fetchall():
            et = r["event_type"]
            if et == "card_rendered":
                stats["rendered"] += 1
            elif et == "card_interaction_started":
                stats["started"] += 1
            elif et == "card_submitted":
                stats["submitted"] += 1
                stats["respondents"].add(r["user_id"])
                sel = db.dj(r["payload"], {}).get("user_selection")
                cfg_now = ((card.get("field_bindings") or {}).get("config") or {})
                if isinstance(sel, list):
                    for item in sel:
                        k = cards.resolve_option_alias(cfg_now, str(item))
                        distribution[k] = distribution.get(k, 0) + 1
                elif sel is not None and not (isinstance(sel, str) and len(sel) > 24):
                    k = cards.resolve_option_alias(cfg_now, str(sel))
                    distribution[k] = distribution.get(k, 0) + 1
        out.append({
            "card_id": card["card_id"], "name": card["name"],
            "question": (card.get("text_templates") or {}).get("prompt") or card["name"],
            "component_type": card["component_type"], "echo_results": card["echo_results"],
            "status": "published" if (card["status"] == "draft" and card["version"] >= 1) else card["status"],
            "rendered": stats["rendered"], "started": stats["started"], "submitted": stats["submitted"],
            "respondents": len(stats["respondents"]),
            "completion_rate": round(stats["submitted"] / stats["rendered"], 3) if stats["rendered"] else None,
            "distribution": dict(sorted(distribution.items(), key=lambda x: -x[1])),
        })
    out.sort(key=lambda x: -x["submitted"])
    return {"window_days": days, "questions": out}


# ============ P2 模型注册与入池 ============

_backfill_tasks = {}


@app.get("/v1/models")
def list_models():
    conn = db.get_conn()
    out = []
    for r in conn.execute("SELECT * FROM models").fetchall():
        m = dict(r)
        m["capabilities"] = db.dj(m["capabilities"], {})
        ref = m.pop("credential_ref", "") or ""
        m["credential_masked"] = (ref[:12] + "****" + ref[-4:]) if len(ref) > 16 else "****"
        m.pop("profile", None)  # 隐藏能力画像不下发（那是被测对象，不是配置）
        task = _backfill_tasks.get(m["model_id"])
        m["backfill"] = {k: task[k] for k in ("status", "done", "total", "cost_est")} if task else None
        out.append(m)
    return {"models": out}


# ---------- 对外接入：API Key 管理（明文仅创建时返回一次，库中只存哈希） ----------

def _mcp_key(product_id: str) -> str:
    import hashlib as _h
    return "sk-mcp-" + _h.md5(("mcp-key:" + product_id).encode()).hexdigest()[:16]


def _product_row(r):
    return {"product_id": r["product_id"], "name": r["name"], "brand_file": r["brand_file"],
            "card_ids": db.dj(r["card_ids"], []), "created_at": r["created_at"],
            "mcp_key": _mcp_key(r["product_id"])}


@app.get("/api/products")
def list_products():
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM products ORDER BY created_at").fetchall()
    return {"products": [_product_row(r) for r in rows]}


@app.post("/api/products")
async def create_product(request: Request):
    """新建产品：名称 + 品牌风格（单选） + 绑定组件（多选）。每个产品一个 MCP 接入点。"""
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name or len(name) > 15:
        return JSONResponse({"error": "产品名称必填，1-15 字"}, status_code=422)
    brand_file = (body.get("brand_file") or "").strip() or "brand-tokens.default.json"
    conn = db.get_conn()
    valid = {r["card_id"] for r in conn.execute("SELECT card_id FROM cards").fetchall()}
    card_ids = [c for c in (body.get("card_ids") or []) if isinstance(c, str) and c in valid]
    if conn.execute("SELECT 1 FROM products WHERE name=?", (name,)).fetchone():
        return JSONResponse({"error": "已有同名产品"}, status_code=409)
    pid = "prod-" + db.new_id()[:8]
    conn.execute("INSERT INTO products (product_id, name, brand_file, card_ids, created_at) VALUES (?,?,?,?,?)",
                 (pid, name, brand_file, db.j(card_ids), db.now_ts()))
    conn.commit()
    db.audit("demo-admin", "product_create", {"product_id": pid, "name": name, "cards": len(card_ids)})
    return {"product_id": pid, "mcp_key": _mcp_key(pid)}


@app.put("/api/products/{product_id}")
async def update_product(product_id: str, request: Request):
    body = await request.json()
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM products WHERE product_id=?", (product_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "产品不存在"}, status_code=404)
    name = (body.get("name") or row["name"]).strip()
    if not name or len(name) > 15:
        return JSONResponse({"error": "产品名称必填，1-15 字"}, status_code=422)
    brand_file = (body.get("brand_file") or row["brand_file"]).strip()
    card_ids = body.get("card_ids")
    if isinstance(card_ids, list):
        valid = {r["card_id"] for r in conn.execute("SELECT card_id FROM cards").fetchall()}
        card_ids = db.j([c for c in card_ids if isinstance(c, str) and c in valid])
    else:
        card_ids = row["card_ids"]
    conn.execute("UPDATE products SET name=?, brand_file=?, card_ids=? WHERE product_id=?",
                 (name, brand_file, card_ids, product_id))
    conn.commit()
    db.audit("demo-admin", "product_update", {"product_id": product_id, "name": name})
    return {"ok": True}


@app.post("/api/products/{product_id}/delete")
async def delete_product(product_id: str):
    conn = db.get_conn()
    row = conn.execute("SELECT name FROM products WHERE product_id=?", (product_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "产品不存在"}, status_code=404)
    conn.execute("DELETE FROM products WHERE product_id=?", (product_id,))
    conn.commit()
    db.audit("demo-admin", "product_delete", {"product_id": product_id, "name": row["name"]})
    return {"ok": True}


@app.get("/api/apikeys")
def list_api_keys():
    conn = db.get_conn()
    keys = [dict(r) for r in conn.execute(
        "SELECT key_id, name, prefix, created_at, last_used FROM api_keys ORDER BY created_at DESC").fetchall()]
    total_calls = conn.execute("SELECT COUNT(*) AS c FROM traces").fetchone()["c"]
    import time as _t2
    calls_7d = conn.execute("SELECT COUNT(*) AS c FROM traces WHERE ts>?", (_t2.time() - 7 * 86400,)).fetchone()["c"]
    return {"keys": keys, "stats": {"total_calls": total_calls, "calls_7d": calls_7d, "key_count": len(keys)}}


@app.post("/api/apikeys")
async def create_api_key(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name or len(name) > 15:
        return JSONResponse({"error": "名称必填，1-15 字"}, status_code=422)
    import hashlib as _h, secrets as _sec
    secret = "sk-live-" + _sec.token_hex(16)
    key_id = db.new_id()[:8]
    conn = db.get_conn()
    conn.execute("INSERT INTO api_keys (key_id, name, secret_hash, prefix, created_at, last_used) VALUES (?,?,?,?,?,NULL)",
                 (key_id, name, _h.sha256(secret.encode()).hexdigest(), secret[:15], db.now_ts()))
    conn.commit()
    db.audit("demo-admin", "apikey_create", {"key_id": key_id, "name": name})
    return {"key_id": key_id, "name": name, "secret": secret}


@app.post("/api/apikeys/{key_id}/delete")
async def delete_api_key(key_id: str):
    conn = db.get_conn()
    n = conn.execute("DELETE FROM api_keys WHERE key_id=?", (key_id,)).rowcount
    conn.commit()
    if not n:
        return JSONResponse({"error": "密钥不存在"}, status_code=404)
    db.audit("demo-admin", "apikey_delete", {"key_id": key_id})
    return {"ok": True}


def _touch_api_key(api_key: str):
    """产品级 Key（sk-live-）调用计数：更新最后使用时间。"""
    if not api_key or not api_key.startswith("sk-live-"):
        return
    import hashlib as _h
    conn = db.get_conn()
    conn.execute("UPDATE api_keys SET last_used=? WHERE secret_hash=?",
                 (db.now_ts(), _h.sha256(api_key.encode()).hexdigest()))
    conn.commit()


def _policy_api_key(policy_id: str) -> str:
    import hashlib as _h
    return "sk-route-" + _h.md5(("route-key:" + policy_id).encode()).hexdigest()[:16]


@app.post("/v1/models/{model_id}/thinking")
async def toggle_thinking(model_id: str, request: Request):
    """思考模式开关：仅支持深度思考的模型可切换。"""
    body = await request.json()
    conn = db.get_conn()
    row = conn.execute("SELECT capabilities FROM models WHERE model_id=?", (model_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "模型不存在"}, status_code=404)
    caps = db.dj(row["capabilities"], {}) or {}
    if not caps.get("thinking"):
        return JSONResponse({"error": "该模型不支持思考模式"}, status_code=409)
    caps["thinking_enabled"] = bool(body.get("enabled"))
    conn.execute("UPDATE models SET capabilities=? WHERE model_id=?", (db.j(caps), model_id))
    conn.commit()
    db.audit("demo-admin", "model_thinking", {"model_id": model_id, "enabled": caps["thinking_enabled"]})
    return {"ok": True, "enabled": caps["thinking_enabled"]}


@app.post("/v1/models/{model_id}/update")
async def update_model_info(model_id: str, request: Request):
    """编辑模型基础信息：当前支持改显示名。"""
    body = await request.json()
    name = (body.get("display_name") or "").strip()
    if not name or len(name) > 24:
        return JSONResponse({"error": "显示名必填，不超过 24 字"}, status_code=422)
    conn = db.get_conn()
    n = conn.execute("UPDATE models SET display_name=? WHERE model_id=?", (name, model_id)).rowcount
    conn.commit()
    if not n:
        return JSONResponse({"error": "模型不存在"}, status_code=404)
    db.audit("demo-admin", "model_rename", {"model_id": model_id, "display_name": name})
    return {"ok": True}


@app.post("/v1/models/{model_id}/delete")
async def delete_model(model_id: str):
    """删除模型：默认兜底模型不可删；历史评测成绩与调用记录保留用于审计。"""
    conn = db.get_conn()
    row = conn.execute("SELECT is_default, display_name FROM models WHERE model_id=?", (model_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "模型不存在"}, status_code=404)
    if row["is_default"]:
        return JSONResponse({"error": "默认兜底模型不能删除，请先把默认切换到其他模型"}, status_code=409)
    conn.execute("DELETE FROM models WHERE model_id=?", (model_id,))
    conn.commit()
    db.audit("demo-admin", "model_delete", {"model_id": model_id, "display_name": row["display_name"]})
    return {"ok": True}


@app.post("/v1/models/{model_id}/profile-data")
async def import_model_profile_data(model_id: str, request: Request):
    """导入模型画像数据（第二步，非必选）：单价与上下文长度。支持 AI 从网页链接自动获取（演示模拟）。"""
    body = await request.json()
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM models WHERE model_id=?", (model_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "模型不存在"}, status_code=404)
    try:
        pin = float(body.get("price_input") or 0)
        pout = float(body.get("price_output") or 0)
        ctx = int(body.get("context_window") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"error": "单价与上下文长度需为数字"}, status_code=422)
    if pin < 0 or pout < 0:
        return JSONResponse({"error": "单价不能为负数"}, status_code=422)
    caps = db.dj(row["capabilities"], {}) or {}
    if ctx > 0:
        caps["context_window"] = ctx
    conn.execute("UPDATE models SET price_input=?, price_output=?, capabilities=? WHERE model_id=?",
                 (pin, pout, db.j(caps), model_id))
    conn.commit()
    db.audit("demo-admin", "model_profile_data", {"model_id": model_id, "price_input": pin, "price_output": pout, "context_window": ctx})
    return {"ok": True}


@app.post("/v1/models")
async def register_model(request: Request):
    body = await request.json()
    required = ["model_id", "display_name", "provider", "endpoint", "credential_ref"]
    missing = [f for f in required if not body.get(f)]
    body.setdefault("price_input", 0)
    body.setdefault("price_output", 0)
    if missing:
        return JSONResponse({"error": f"缺少必填字段：{', '.join(missing)}"}, status_code=422)
    try:
        pin, pout = float(body["price_input"] or 0), float(body["price_output"] or 0)
        if pin < 0 or pout < 0:
            return JSONResponse({"error": "单价不能为负数"}, status_code=422)
        lat = int(body.get("latency_ms_base", 800))
        if lat <= 0:
            return JSONResponse({"error": "基准延迟必须为正整数"}, status_code=422)
    except (TypeError, ValueError):
        return JSONResponse({"error": "单价与延迟必须是数字"}, status_code=422)
    conn = db.get_conn()
    if conn.execute("SELECT 1 FROM models WHERE model_id=?", (body["model_id"],)).fetchone():
        return JSONResponse({"error": "model_id 已存在"}, status_code=409)
    conn.execute(
        "INSERT INTO models (model_id, display_name, provider, endpoint, credential_ref, price_input, "
        "price_output, capabilities, status, bank_coverage, latency_ms_base, profile) VALUES (?,?,?,?,?,?,?,?,?,0,?,?)",
        (body["model_id"], body["display_name"], body["provider"], body["endpoint"], body["credential_ref"],
         float(body["price_input"]), float(body["price_output"]),
         db.j(body.get("capabilities") or {"tool_call": True, "streaming": True, "context_window": 32768}),
         "registering", int(body.get("latency_ms_base", 800)),
         db.j(body.get("profile") or {"general": 0.7})))
    conn.commit()
    db.audit("demo-admin", "model_register", {"model_id": body["model_id"]})
    return {"ok": True, "note": "入池未完成的模型不参与线上路由。请触发 bank 回填任务。"}


@app.post("/v1/models/{model_id}/backfill")
async def start_backfill(model_id: str):
    """模型入池任务：带进度、成本预估、可暂停（§3.3 落差四）。"""
    conn = db.get_conn()
    m = conn.execute("SELECT * FROM models WHERE model_id=?", (model_id,)).fetchone()
    if not m:
        return JSONResponse({"error": "not_found"}, status_code=404)
    total = conn.execute("SELECT COUNT(*) AS c FROM bank_queries WHERE tenant_id IS NULL").fetchone()["c"]
    existing = _backfill_tasks.get(model_id)
    if existing and existing["status"] == "running":
        return {"task": existing}
    cost_est = round(total * 300 / 1e6 * (m["price_input"] + m["price_output"]), 4)
    task = {"model_id": model_id, "status": "running", "done": (existing or {}).get("done", 0),
            "total": total, "cost_est": cost_est}
    _backfill_tasks[model_id] = task
    asyncio.create_task(_run_backfill(model_id))
    return {"task": task}


@app.post("/v1/models/{model_id}/backfill/pause")
def pause_backfill(model_id: str):
    task = _backfill_tasks.get(model_id)
    if task and task["status"] == "running":
        task["status"] = "paused"
    return {"task": task}


@app.get("/v1/models/{model_id}/backfill/status")
def backfill_status(model_id: str):
    return {"task": _backfill_tasks.get(model_id)}


async def _run_backfill(model_id: str):
    conn = db.get_conn()
    m = dict(conn.execute("SELECT * FROM models WHERE model_id=?", (model_id,)).fetchone())
    profile = db.dj(m["profile"], {"general": 0.7})
    task = _backfill_tasks[model_id]
    rows = conn.execute("SELECT * FROM bank_queries WHERE tenant_id IS NULL ORDER BY query_id").fetchall()
    for i, q in enumerate(rows):
        if i < task["done"]:
            continue
        if task["status"] != "running":
            return  # 暂停：断点保留，可续跑
        domain = (db.dj(q["domain_tags"], []) or ["general"])[0]
        correct = mockmodels.is_correct(model_id, profile, q["query_text"], domain)
        content, _ = mockmodels.gen_structured(q["query_text"], domain, correct)
        conn.execute(
            "INSERT OR REPLACE INTO bank_responses (query_id, model_id, response_embedding, completion_tokens, "
            "label_value, label_confidence, label_source, label_kind, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (q["query_id"], model_id, db.j(embeddings.embed(content)), max(30, int(len(content) * 1.5)),
             1.0 if correct else 0.0, 1.0, "ground_truth", "capability", db.now_ts(), db.now_ts()))
        task["done"] = i + 1
        coverage = task["done"] / max(1, task["total"])
        conn.execute("UPDATE models SET bank_coverage=? WHERE model_id=?", (round(coverage, 4), model_id))
        conn.commit()
        await asyncio.sleep(0.02)
    task["status"] = "completed"
    conn.execute("UPDATE models SET bank_coverage=1.0, status='active' WHERE model_id=?", (model_id,))
    conn.commit()
    db.audit("system", "model_backfill_completed", {"model_id": model_id, "total": task["total"]})


@app.post("/v1/models/{model_id}/status")
async def set_model_status(model_id: str, request: Request):
    body = await request.json()
    status = body.get("status")
    if status not in ("active", "paused", "retired"):
        return JSONResponse({"error": "非法状态"}, status_code=422)
    conn = db.get_conn()
    m = conn.execute("SELECT bank_coverage, is_default FROM models WHERE model_id=?", (model_id,)).fetchone()
    if not m:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if status == "active" and (m["bank_coverage"] or 0) < 1.0:
        return JSONResponse({"error": "bank 覆盖率未达 100%，入池未完成的模型不得上线"}, status_code=409)
    if status != "active" and m["is_default"]:
        # 走查 R1：默认兜底模型是所有降级路径的终点，不能被暂停/退役
        return JSONResponse({"error": "默认兜底模型不能停用或退役，请先把默认兜底切换到其他在线模型"}, status_code=409)
    conn.execute("UPDATE models SET status=? WHERE model_id=?", (status, model_id))
    conn.commit()
    db.audit("demo-admin", "model_status_change", {"model_id": model_id, "status": status})
    return {"ok": True}


@app.post("/v1/models/{model_id}/set-default")
def set_default_model(model_id: str):
    """指定默认兜底模型：路由/手动模式故障时的最终切换目标（标书 F-5-04）。"""
    conn = db.get_conn()
    m = conn.execute("SELECT status FROM models WHERE model_id=?", (model_id,)).fetchone()
    if not m:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if m["status"] != "active":
        return JSONResponse({"error": "仅在线模型可设为默认兜底"}, status_code=409)
    conn.execute("UPDATE models SET is_default=0")
    conn.execute("UPDATE models SET is_default=1 WHERE model_id=?", (model_id,))
    conn.commit()
    db.audit("demo-admin", "model_set_default", {"model_id": model_id})
    return {"ok": True}


@app.post("/v1/models/{model_id}/credential/delete")
def delete_credential(model_id: str):
    """凭证删除路径：模型转为 paused，进行中的会话由路由侧切换备选（§3.4）。"""
    conn = db.get_conn()
    m = conn.execute("SELECT is_default FROM models WHERE model_id=?", (model_id,)).fetchone()
    if not m:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if m["is_default"]:
        return JSONResponse({"error": "默认兜底模型的凭证不能删除，请先把默认兜底切换到其他在线模型"}, status_code=409)
    conn.execute("UPDATE models SET credential_ref='', status='paused' WHERE model_id=?", (model_id,))
    conn.commit()
    db.audit("demo-admin", "credential_delete", {"model_id": model_id})
    return {"ok": True, "note": "凭证已删除，模型已停用。正在进行的会话将自动切换备选模型并在 Trace 中标记。"}


# ============ LLM-as-judge 离线批标（§3.5 标签链路之四） ============

_judge_task = {"status": "idle", "done": 0, "total": 0, "labeled": 0}


LABEL_SOURCES = [
    {"source": "explicit_preference", "name": "多回答择优", "confidence": 0.9,
     "desc": "多模型回答里用户选出更好的一份，最高质量的成对标签"},
    {"source": "explicit_binary", "name": "赞踩反馈", "confidence": 0.6,
     "desc": "每条回答的赞 / 踩，按能力与偏好分组"},
    {"source": "implicit_behavior", "name": "隐式行为", "confidence": 0.25,
     "desc": "复制回答（弱正）、换模型重答（弱负）等行为信号，低置信自动降权"},
    {"source": "llm_judge", "name": "AI 评审补标", "confidence": 0.5,
     "desc": "给无标签的历史请求批量打伪标签，覆盖率不足时使用"},
]


def _source_enabled(conn, source):
    r = conn.execute("SELECT v FROM kv_settings WHERE k=?", (f"label_source_off:{source}",)).fetchone()
    return not (r and r["v"] == "1")


@app.get("/api/labels/summary")
def labels_summary():
    """反馈优化页总览：信号源构成 / 闸门状态 / 距画像更新的增量 / 待评测题量。"""
    conn = db.get_conn()
    since30 = db.now_ts() - 30 * 86400
    rebuild_row = conn.execute(
        "SELECT ts FROM audit_log WHERE action='profile_rebuild' ORDER BY id DESC LIMIT 1").fetchone()
    last_rebuild = rebuild_row["ts"] if rebuild_row else None
    out_sources = []
    for m in LABEL_SOURCES:
        n30 = conn.execute(
            "SELECT COUNT(*) c FROM labels WHERE tenant_id=? AND source=? AND status='admitted' AND created_at>?",
            (seed.TENANT, m["source"], since30)).fetchone()["c"]
        out_sources.append({**m, "count_30d": n30, "enabled": _source_enabled(conn, m["source"])})
    since_rebuild = conn.execute(
        "SELECT COUNT(*) c FROM labels WHERE tenant_id=? AND status='admitted' AND created_at>?",
        (seed.TENANT, last_rebuild or 0)).fetchone()["c"]
    pending_eval = conn.execute(
        "SELECT COUNT(*) c FROM bank_queries q WHERE q.tenant_id=? AND NOT EXISTS "
        "(SELECT 1 FROM bank_responses r WHERE r.query_id=q.query_id)", (seed.TENANT,)).fetchone()["c"]
    return {"sources": out_sources, "since_rebuild": since_rebuild,
            "last_rebuild_ts": last_rebuild, "pending_eval": pending_eval}


@app.post("/api/labels/sources")
async def toggle_label_source(request: Request):
    body = await request.json()
    source, enabled = body.get("source"), bool(body.get("enabled"))
    if source not in {m["source"] for m in LABEL_SOURCES}:
        return JSONResponse({"error": "未知信号源"}, status_code=422)
    conn = db.get_conn()
    conn.execute("INSERT OR REPLACE INTO kv_settings (k, v) VALUES (?,?)",
                 (f"label_source_off:{source}", "0" if enabled else "1"))
    conn.commit()
    db.audit("demo-admin", "label_source_toggle", {"source": source, "enabled": enabled})
    return {"ok": True}


@app.post("/v1/bank/eval-pending")
def bank_eval_pending():
    """评测回填（论文闭环：导题 → 各在线模型作答 → judge 模型择优打分入库）。"""
    judge = _get_setting("judge_model")
    if not judge:
        return JSONResponse({"error": "未配置路由决策模型 模型：请先在「调度策略」页选择用于评审的模型"}, status_code=409)
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT q.query_id, q.query_text, q.domain_tags FROM bank_queries q WHERE q.tenant_id=? "
        "AND (q.source IS NULL OR q.source != 'reflow_staged') AND NOT EXISTS "
        "(SELECT 1 FROM bank_responses r WHERE r.query_id=q.query_id)", (seed.TENANT,)).fetchall()
    models = conn.execute("SELECT model_id, profile FROM models WHERE status='active'").fetchall()
    n_resp = 0
    for q in rows:
        domain = (db.dj(q["domain_tags"], ["general"]) or ["general"])[0]
        for m in models:
            profile = db.dj(m["profile"], {})
            correct = mockmodels.is_correct(m["model_id"], profile, q["query_text"], domain)
            content, _ = mockmodels.gen_structured(q["query_text"], domain, correct)
            conn.execute(
                "INSERT INTO bank_responses (query_id, model_id, response_embedding, completion_tokens, "
                "label_value, label_confidence, label_source, label_kind, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (q["query_id"], m["model_id"], db.j(embeddings.embed(content)), max(30, int(len(content) * 1.5)),
                 1.0 if correct else 0.0, 0.7, "model_eval", "capability", db.now_ts(), db.now_ts()))
            n_resp += 1
    conn.commit()
    # 评测轮次：本次覆盖到的每个场景 +1 轮
    rounds = db.dj(_get_setting("scene_rounds"), {}) or {}
    for dom in {(db.dj(q["domain_tags"], ["general"]) or ["general"])[0] for q in rows}:
        rounds[dom] = rounds.get(dom, 0) + 1
    if rows:
        _set_setting("scene_rounds", db.j(rounds))
    db.audit("demo-admin", "bank_eval_pending", {"queries": len(rows), "responses": n_resp, "judge_model": judge})
    return {"queries": len(rows), "responses": n_resp, "judge_model": judge}


@app.post("/v1/labels/judge/run")
async def run_judge():
    if not _source_enabled(db.get_conn(), "llm_judge"):
        return JSONResponse({"error": "AI 评审信号源已关闭，请先在反馈优化页开启"}, status_code=409)
    if not _get_setting("judge_model"):
        return JSONResponse({"error": "未配置路由决策模型 模型：请先在「调度策略」页选择用于评审的模型"}, status_code=409)
    """离线批标任务：对尚无标签的历史 Trace 用 judge 模型打伪标签（confidence 0.5）。
    演示环境 judge 为模拟（与真实对错约 80% 一致）；生产替换为真实 LLM 评审调用。"""
    if _judge_task["status"] == "running":
        return {"task": _judge_task}
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT t.* FROM traces t LEFT JOIN labels l ON t.trace_id=l.trace_id "
        "WHERE l.label_id IS NULL AND t.final_model IS NOT NULL AND t.query_text != '' "
        "GROUP BY t.trace_id ORDER BY t.ts DESC LIMIT 200").fetchall()
    _judge_task.update({"status": "running", "done": 0, "total": len(rows), "labeled": 0})
    asyncio.create_task(_run_judge([dict(r) for r in rows]))
    return {"task": _judge_task}


@app.get("/v1/labels/judge/status")
def judge_status():
    return {"task": _judge_task}


async def _run_judge(trace_rows):
    import hashlib as _hl
    conn = db.get_conn()
    models_by_id = {}
    for r in conn.execute("SELECT model_id, profile FROM models").fetchall():
        models_by_id[r["model_id"]] = db.dj(r["profile"], {"general": 0.7})
    for i, t in enumerate(trace_rows):
        mid = t["final_model"]
        profile = models_by_id.get(mid)
        if profile:
            domain = mockmodels.classify_domain(t["query_text"])
            actual = mockmodels.is_correct(mid, profile, t["query_text"], domain)
            # judge 有自身误差：与真实对错约 80% 一致
            agree = int(_hl.md5(("judge" + t["trace_id"]).encode()).hexdigest()[:4], 16) % 100 < 80
            verdict = actual if agree else (not actual)
            spec = {"model_id": mid, "value": 1.0 if verdict else 0.0,
                    "confidence": 0.5, "kind": "capability", "source": "llm_judge"}
            conn.execute(
                "INSERT INTO labels (label_id, event_id, trace_id, tenant_id, model_id, label_kind, value, "
                "confidence, source, status, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (db.new_id(), None, t["trace_id"], t["tenant_id"], mid, "capability",
                 spec["value"], 0.5, "llm_judge", "admitted", None, db.now_ts()))
            events._apply_to_bank(t["tenant_id"], _row_like(t), spec, bool(t["is_explore"]))
            traces.add_span(t["trace_id"], "label_emit", {
                "component_type": "llm_judge", "source": "llm_judge",
                "verdict": "correct" if verdict else "incorrect"})
            _judge_task["labeled"] += 1
        _judge_task["done"] = i + 1
        if i % 10 == 0:
            await asyncio.sleep(0.05)
    conn.commit()
    _judge_task["status"] = "completed"
    db.audit("system", "llm_judge_batch", {"labeled": _judge_task["labeled"], "total": _judge_task["total"]})


def _row_like(d: dict):
    """dict 适配 sqlite3.Row 的取值方式，供 events._apply_to_bank 复用。"""
    class _R:
        def __init__(self, data): self._d = data
        def __getitem__(self, k): return self._d.get(k)
    return _R(d)


# ============ P2 策略管理 ============

@app.get("/v1/policies")
def list_policies():
    conn = db.get_conn()
    out = []
    for r in conn.execute("SELECT * FROM policies ORDER BY scope, policy_id").fetchall():
        p = dict(r)
        p["params"] = db.dj(p["params"], {})
        p["model_whitelist"] = db.dj(p["model_whitelist"], [])
        p["budget_cap"] = db.dj(p["budget_cap"], {})
        p["api_key"] = _policy_api_key(p["policy_id"])
        out.append(p)
    return {"policies": out}


@app.post("/v1/policies")
async def create_policy(request: Request):
    """新增路由策略：名称 + 三个主参数（成本上限 / 候选模型 / 聚合值），其余参数继承全局默认。"""
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name or len(name) > 15:
        return JSONResponse({"error": "策略名称必填，1-15 字"}, status_code=422)
    conn = db.get_conn()
    base = conn.execute("SELECT * FROM policies WHERE scope='global' LIMIT 1").fetchone()
    params = db.dj(base["params"], {}) if base else {"K": 3, "N_base": 50, "beta": 0.5, "gamma": 0.95,
                                                     "eps": 0.5, "sigma": 0.3, "delta": 0.2, "t": 0.8, "max_agg_tokens": 13000}
    if body.get("t") is not None:
        params["t"] = max(0.0, min(1.0, float(body["t"])))
    if body.get("alpha") is not None:
        params["alpha"] = max(0.0, min(1.0, float(body["alpha"])))
    if body.get("profile_w") is not None:
        params["profile_w"] = max(1.0, min(2.0, float(body["profile_w"])))
    if body.get("fallback_model"):
        params["fallback_model"] = body["fallback_model"]
    policy_id = "policy-" + db.new_id()[:8]
    budget = {"daily_usd": float(body["daily_usd"])} if body.get("daily_usd") not in (None, "") else {}
    conn.execute(
        "INSERT INTO policies (policy_id, name, scope, tenant_id, scene, params, latency_tier, "
        "allow_aggregation, explore_ratio, model_whitelist, budget_cap, enabled, ab_group, ab_split, version) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,1,NULL,50,1)",
        (policy_id, name, "custom", seed.TENANT, None, db.j(params),
         body.get("latency_tier") or "balanced", 1 if body.get("allow_aggregation", True) else 0,
         float(body.get("explore_ratio", 0.05)), db.j(body.get("model_whitelist") or []), db.j(budget)))
    conn.commit()
    db.audit("demo-admin", "policy_create", {"policy_id": policy_id, "name": name})
    return {"policy_id": policy_id, "api_key": _policy_api_key(policy_id)}


@app.post("/v1/policies/{policy_id}/delete")
async def delete_policy(policy_id: str):
    conn = db.get_conn()
    row = conn.execute("SELECT scope, name FROM policies WHERE policy_id=?", (policy_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "策略不存在"}, status_code=404)
    if row["scope"] == "global":
        return JSONResponse({"error": "默认策略不能删除"}, status_code=409)
    conn.execute("DELETE FROM policies WHERE policy_id=?", (policy_id,))
    conn.commit()
    db.audit("demo-admin", "policy_delete", {"policy_id": policy_id, "name": row["name"]})
    return {"ok": True}


@app.post("/v1/policies/{policy_id}/duplicate")
async def duplicate_policy(policy_id: str):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM policies WHERE policy_id=?", (policy_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "策略不存在"}, status_code=404)
    new_id2 = "policy-" + db.new_id()[:8]
    name = (row["name"] or "策略") + " 副本"
    conn.execute(
        "INSERT INTO policies (policy_id, name, scope, tenant_id, scene, params, latency_tier, "
        "allow_aggregation, explore_ratio, model_whitelist, budget_cap, enabled, ab_group, ab_split, version) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,1,NULL,50,1)",
        (new_id2, name[:20], "custom", seed.TENANT, None, row["params"], row["latency_tier"],
         row["allow_aggregation"], row["explore_ratio"], row["model_whitelist"], row["budget_cap"]))
    conn.commit()
    db.audit("demo-admin", "policy_duplicate", {"from": policy_id, "to": new_id2})
    return {"policy_id": new_id2, "name": name[:20]}


@app.put("/v1/policies/{policy_id}")
async def update_policy(policy_id: str, request: Request):
    body = await request.json()
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM policies WHERE policy_id=?", (policy_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    # 服务端守门：策略是线上路由的直接输入，坏值即坏路由
    verrs = []
    if "latency_tier" in body and body["latency_tier"] not in ("fast", "balanced", "quality"):
        verrs.append("latency_tier 必须为 fast / balanced / quality")
    try:
        er = float(body.get("explore_ratio", row["explore_ratio"]))
        if not (0 <= er <= 1):
            verrs.append("探索预算必须在 0 ~ 1 之间")
    except (TypeError, ValueError):
        verrs.append("探索预算必须是数字")
    bc = body.get("budget_cap")
    if bc is not None:
        if not isinstance(bc, dict):
            verrs.append("budget_cap 必须是对象")
        else:
            for k, v in bc.items():
                try:
                    if v is not None and float(v) < 0:
                        verrs.append(f"预算 {k} 不能为负数")
                except (TypeError, ValueError):
                    verrs.append(f"预算 {k} 必须是数字")
    prm = body.get("params")
    if prm is not None and not isinstance(prm, dict):
        verrs.append("params 必须是对象")
    if verrs:
        return JSONResponse({"error": "；".join(verrs)}, status_code=422)
    warning = None
    try:
        if float(body.get("explore_ratio", row["explore_ratio"])) > 0.2:
            warning = "探索预算超过 20%，会显著增加成本与质量波动，请确认"
    except (TypeError, ValueError):
        pass
    new_version = row["version"] + 1
    params = {**db.dj(row["params"], {}), **(body.get("params") or {})}
    if body.get("name") and 1 <= len(body["name"].strip()) <= 15:
        conn.execute("UPDATE policies SET name=? WHERE policy_id=?", (body["name"].strip(), policy_id))
    conn.execute(
        "UPDATE policies SET params=?, latency_tier=?, allow_aggregation=?, explore_ratio=?, "
        "model_whitelist=?, budget_cap=?, enabled=?, version=? WHERE policy_id=?",
        (db.j(params), body.get("latency_tier", row["latency_tier"]),
         1 if body.get("allow_aggregation", row["allow_aggregation"]) else 0,
         float(body.get("explore_ratio", row["explore_ratio"])),
         db.j(body.get("model_whitelist", db.dj(row["model_whitelist"], []))),
         db.j(body.get("budget_cap", db.dj(row["budget_cap"], {}))),
         1 if body.get("enabled", row["enabled"]) else 0, new_version, policy_id))
    conn.execute("INSERT OR REPLACE INTO policy_history (policy_id, version, snapshot, ts) VALUES (?,?,?,?)",
                 (policy_id, new_version, db.j({"params": params, "latency_tier": body.get("latency_tier", row["latency_tier"]),
                                                "explore_ratio": body.get("explore_ratio", row["explore_ratio"]),
                                                "budget_cap": body.get("budget_cap")}), db.now_ts()))
    conn.commit()
    db.audit("demo-admin", "policy_update", {"policy_id": policy_id, "version": new_version})
    return {"ok": True, "version": new_version, "warning": warning}


@app.get("/v1/policies/{policy_id}/history")
def policy_history(policy_id: str):
    conn = db.get_conn()
    rows = [{"version": r["version"], "snapshot": db.dj(r["snapshot"], {}), "ts": r["ts"]}
            for r in conn.execute("SELECT * FROM policy_history WHERE policy_id=? ORDER BY version DESC",
                                  (policy_id,)).fetchall()]
    return {"history": rows}


@app.post("/v1/policies/{policy_id}/rollback")
async def rollback_policy(policy_id: str, request: Request):
    body = await request.json()
    target = int(body.get("version"))
    conn = db.get_conn()
    snap = conn.execute("SELECT snapshot FROM policy_history WHERE policy_id=? AND version=?",
                        (policy_id, target)).fetchone()
    if not snap:
        return JSONResponse({"error": "目标版本不存在"}, status_code=404)
    s = db.dj(snap["snapshot"], {})
    row = conn.execute("SELECT version FROM policies WHERE policy_id=?", (policy_id,)).fetchone()
    new_version = row["version"] + 1
    conn.execute("UPDATE policies SET params=?, latency_tier=?, explore_ratio=?, version=? WHERE policy_id=?",
                 (db.j(s.get("params", {})), s.get("latency_tier", "balanced"),
                  float(s.get("explore_ratio", 0.05)), new_version, policy_id))
    conn.execute("INSERT OR REPLACE INTO policy_history (policy_id, version, snapshot, ts) VALUES (?,?,?,?)",
                 (policy_id, new_version, snap["snapshot"], db.now_ts()))
    conn.commit()
    db.audit("demo-admin", "policy_rollback", {"policy_id": policy_id, "from": target, "new_version": new_version})
    return {"ok": True, "version": new_version}


@app.post("/v1/policies/ab")
async def create_ab(request: Request):
    """基于现有策略创建 A/B 实验：原策略为 A 组，副本应用 overrides 为 B 组。"""
    body = await request.json()
    base_id = body.get("base_policy_id")
    conn = db.get_conn()
    base = conn.execute("SELECT * FROM policies WHERE policy_id=?", (base_id,)).fetchone()
    if not base:
        return JSONResponse({"error": "not_found"}, status_code=404)
    split = int(body.get("split", 50))
    b_id = base_id + "-ab-b"
    params_b = {**db.dj(base["params"], {}), **(body.get("params_override") or {})}
    conn.execute("UPDATE policies SET ab_group='A', ab_split=? WHERE policy_id=?", (split, base_id))
    conn.execute(
        "INSERT OR REPLACE INTO policies (policy_id, name, scope, tenant_id, scene, params, latency_tier, "
        "allow_aggregation, explore_ratio, model_whitelist, budget_cap, enabled, ab_group, ab_split, version) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'B',?,1)",
        (b_id, (base["name"] or base_id) + " · B组", base["scope"], base["tenant_id"], base["scene"],
         db.j(params_b), body.get("latency_tier", base["latency_tier"]),
         base["allow_aggregation"], base["explore_ratio"], base["model_whitelist"], base["budget_cap"],
         100 - split))
    conn.commit()
    db.audit("demo-admin", "ab_create", {"base": base_id, "b": b_id, "split": split})
    return {"ok": True, "a": base_id, "b": b_id}


@app.post("/v1/policies/ab/stop")
async def stop_ab(request: Request):
    body = await request.json()
    base_id = body.get("base_policy_id")
    conn = db.get_conn()
    conn.execute("UPDATE policies SET ab_group=NULL WHERE policy_id=?", (base_id,))
    conn.execute("UPDATE policies SET enabled=0, ab_group=NULL WHERE policy_id=?", (base_id + "-ab-b",))
    conn.commit()
    db.audit("demo-admin", "ab_stop", {"base": base_id})
    return {"ok": True}


# ============ bank ============

@app.get("/v1/bank/health")
def bank_health(tenant_id: str = None):
    return dashboard.bank_health(tenant_id or seed.TENANT)


def _get_setting(key, default=None):
    row = db.get_conn().execute("SELECT v FROM kv_settings WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def _set_setting(key, val):
    conn = db.get_conn()
    conn.execute("INSERT INTO kv_settings (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=?", (key, val, val))
    conn.commit()


@app.get("/api/settings/judge-model")
def get_judge_model():
    info = db.dj(_get_setting("judge_model_info"), None)
    mid = _get_setting("judge_model") or None
    if not info and mid:
        info = {"model_id": mid}  # 兼容旧数据：只存了 id
    return {"judge": info if (info and info.get("model_id")) else None}


@app.post("/api/settings/judge-model")
async def set_judge_model(request: Request):
    """Judge 模型独立接入（与业务模型池分离）：显示名 / 模型 ID / 接口地址 / 凭证引用。传空 model_id 为移除。"""
    body = await request.json()
    mid = (body.get("model_id") or "").strip()
    if not mid:
        _set_setting("judge_model", "")
        _set_setting("judge_model_info", "")
        db.audit("demo-admin", "judge_model_set", {"model_id": "(移除)"})
        return {"ok": True, "judge": None}
    import re as _re3
    if not body.get("display_name"):
        return JSONResponse({"error": "请填写显示名"}, status_code=422)
    if not _re3.fullmatch(r"[a-z0-9][a-z0-9-]{1,23}", mid):
        return JSONResponse({"error": "模型 ID 需为 2-24 位小写字母、数字或短横线"}, status_code=422)
    if not _re3.fullmatch(r"https://\S+", (body.get("endpoint") or "")):
        return JSONResponse({"error": "接口地址必须是 https:// 开头的完整 URL"}, status_code=422)
    if not _re3.fullmatch(r"vault://\S+", (body.get("credential_ref") or "")):
        return JSONResponse({"error": "凭证需为 vault:// 引用（密钥不明文入库）"}, status_code=422)
    info = {"model_id": mid, "display_name": body["display_name"].strip(),
            "endpoint": body["endpoint"].strip(), "credential_ref": body["credential_ref"].strip()}
    _set_setting("judge_model", mid)
    _set_setting("judge_model_info", db.j(info))
    db.audit("demo-admin", "judge_model_set", {"model_id": mid, "display_name": info["display_name"]})
    return {"ok": True, "judge": info}


@app.get("/v1/bank/scenes")
def bank_scenes():
    """场景数据集总览：按业务场景分组统计冷启动 / 自动收集（已并入 + 待并入）/ 导入 / 待评测。"""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT q.query_id, q.tenant_id, q.domain_tags, q.source, q.created_at, "
        "EXISTS(SELECT 1 FROM bank_responses r WHERE r.query_id=q.query_id) AS has_resp "
        "FROM bank_queries q WHERE q.tenant_id IS NULL OR q.tenant_id=?", (seed.TENANT,)).fetchall()
    scenes = {}
    def blank(dom):
        return {"domain": dom, "cold": 0, "reflow": 0, "staged": 0, "imported": 0, "pending": 0, "last_ts": 0}
    for r in rows:
        dom = (db.dj(r["domain_tags"], ["general"]) or ["general"])[0]
        sc = scenes.setdefault(dom, blank(dom))
        if r["source"] == "reflow_staged":
            sc["staged"] += 1
            continue  # 待并入不计入数据集，也不进待评测
        if r["tenant_id"] is None:
            sc["cold"] += 1
        elif r["source"] == "reflow":
            sc["reflow"] += 1
        else:
            sc["imported"] += 1
        if not r["has_resp"]:
            sc["pending"] += 1
        sc["last_ts"] = max(sc["last_ts"], r["created_at"] or 0)
    # 自定义场景（即使还没有题目也在表中出现）
    custom = db.dj(_get_setting("custom_scenes"), []) or []
    for c in custom:
        scenes.setdefault(c["key"], blank(c["key"]))
    rounds = db.dj(_get_setting("scene_rounds"), {}) or {}
    for sc in scenes.values():
        sc["total"] = sc["cold"] + sc["reflow"] + sc["imported"]
        sc["rounds"] = rounds.get(sc["domain"], 0)
    out = sorted(scenes.values(), key=lambda x: -x["total"])
    _ji = db.dj(_get_setting("judge_model_info"), None) or {}
    _jm = _get_setting("judge_model") or None
    return {"scenes": out, "custom_scenes": custom,
            "judge_model": _jm, "judge_name": _ji.get("display_name") or _jm}


@app.post("/api/scenes")
async def add_scene(request: Request):
    """自定义业务场景：名称 + 描述（描述供分类与 Judge 评审理解场景用）。自动收集按内置分类，自定义场景通过导入积累。"""
    body = await request.json()
    name = (body.get("name") or "").strip()
    desc = (body.get("desc") or "").strip()
    if not name or len(name) > 12:
        return JSONResponse({"error": "场景名必填，不超过 12 字"}, status_code=422)
    custom = db.dj(_get_setting("custom_scenes"), []) or []
    if any(c["name"] == name for c in custom):
        return JSONResponse({"error": "已有同名场景"}, status_code=409)
    key = "custom-" + db.new_id()[:6]
    custom.append({"key": key, "name": name, "desc": desc})
    _set_setting("custom_scenes", db.j(custom))
    db.audit("demo-admin", "scene_add", {"key": key, "name": name})
    return {"ok": True, "scene": {"key": key, "name": name, "desc": desc}}


_import_task = {"status": "idle", "done": 0, "total": 0, "imported": 0, "skipped": 0, "invalid": 0, "domain": None}


@app.post("/v1/bank/import/start")
async def bank_import_start(request: Request):
    """异步导入：客户端解析出 items 后提交，服务端逐条校验（格式 / 场景 / 去重）并入库，进度可轮询。"""
    if _import_task["status"] == "running":
        return JSONResponse({"error": "已有导入任务进行中"}, status_code=409)
    body = await request.json()
    items = body.get("items") or []
    if not items:
        return JSONResponse({"error": "没有可导入的数据"}, status_code=422)
    if len(items) > 5000:
        return JSONResponse({"error": f"单次最多导入 5000 条（当前 {len(items)} 条），请分批导入"}, status_code=422)
    if body.get("replace") and body.get("domain"):
        if not any((it.get("query") or "").strip() for it in items):
            return JSONResponse({"error": "替换模式下没有解析到有效题目，未清空原数据"}, status_code=422)
        conn0 = db.get_conn()
        ids0 = _scene_query_ids(conn0, body["domain"])
        _delete_bank_queries(conn0, ids0)
        conn0.commit()
        db.audit("demo-admin", "bank_scene_replace", {"domain": body["domain"], "cleared": len(ids0)})
    _import_task.update({"status": "running", "done": 0, "total": len(items),
                         "imported": 0, "skipped": 0, "invalid": 0, "domain": body.get("domain")})
    asyncio.create_task(_run_import(items, body.get("tenant_id") or seed.TENANT))
    return {"task": _import_task}


@app.get("/v1/bank/import/status")
def bank_import_status():
    return {"task": _import_task}


async def _run_import(items, tenant_id):
    try:
        await _run_import_inner(items, tenant_id)
    except Exception as e:
        # 任务异常必须落 done，否则全局导入任务永远锁在 running
        _import_task["status"] = "done"
        _import_task["error"] = str(e)[:200]
        db.audit("demo-admin", "bank_import_failed", {"error": str(e)[:200]})


async def _run_import_inner(items, tenant_id):
    conn = db.get_conn()
    custom = db.dj(_get_setting("custom_scenes"), []) or []
    valid_scenes = set(mockmodels.DOMAIN_KEYWORDS.keys()) | {"general", "chat"} | {c["key"] for c in custom}
    existing = {row["query_text"].strip() for row in conn.execute(
        "SELECT query_text FROM bank_queries WHERE tenant_id=? OR tenant_id IS NULL", (tenant_id,)).fetchall()}
    for it in items:
        await asyncio.sleep(0.04)  # 演示：模拟逐条校验耗时
        _import_task["done"] += 1
        q = (it.get("query") or "").strip()
        dom = (it.get("domain") or "general").strip() or "general"
        if not q or len(q) > 500 or dom not in valid_scenes:
            _import_task["invalid"] += 1
            continue
        if q in existing:
            _import_task["skipped"] += 1
            continue
        existing.add(q)
        qid = "imp-" + db.new_id()[:8]
        conn.execute(
            "INSERT INTO bank_queries (query_id, tenant_id, embedding, text_ref, query_text, domain_tags, "
            "created_at, ttl_days, source, ideal) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (qid, tenant_id, db.j(embeddings.embed(q)), f"vault://import/{qid}", q,
             db.j([dom]), db.now_ts(), 365, "tenant_imported", (it.get("ideal") or "").strip() or None))
        for mid, val in (it.get("labels") or {}).items():
            conn.execute(
                "INSERT INTO bank_responses (query_id, model_id, response_embedding, completion_tokens, "
                "label_value, label_confidence, label_source, label_kind, created_at, updated_at) "
                "VALUES (?,?,NULL,0,?,1.0,'ground_truth','capability',?,?)",
                (qid, mid, float(val), db.now_ts(), db.now_ts()))
        _import_task["imported"] += 1
    conn.commit()
    _import_task["status"] = "done"
    db.audit("demo-admin", "bank_import", {"count": _import_task["imported"], "skipped": _import_task["skipped"],
                                           "invalid": _import_task["invalid"], "tenant_id": tenant_id, "async": True})


@app.post("/v1/bank/staged/relabel")
async def bank_staged_relabel(request: Request):
    """回流校验：切换某条待并入数据的场景标签。"""
    body = await request.json()
    qid = body.get("query_id") or ""
    dom = (body.get("domain") or "").strip()
    custom = db.dj(_get_setting("custom_scenes"), []) or []
    valid_scenes = set(mockmodels.DOMAIN_KEYWORDS.keys()) | {"general", "chat"} | {c["key"] for c in custom}
    if dom not in valid_scenes:
        return JSONResponse({"error": "未知场景"}, status_code=422)
    conn = db.get_conn()
    n = conn.execute("UPDATE bank_queries SET domain_tags=? WHERE tenant_id=? AND source='reflow_staged' AND query_id=?",
                     (db.j([dom]), seed.TENANT, qid)).rowcount
    conn.commit()
    if not n:
        return JSONResponse({"error": "条目不存在或已处理"}, status_code=404)
    return {"ok": True}


def _scene_query_ids(conn, key):
    ids = []
    for r in conn.execute("SELECT query_id, domain_tags FROM bank_queries WHERE tenant_id IS NULL OR tenant_id=?",
                          (seed.TENANT,)).fetchall():
        if (db.dj(r["domain_tags"], ["general"]) or ["general"])[0] == key:
            ids.append(r["query_id"])
    return ids


def _delete_bank_queries(conn, ids):
    for qid in ids:
        conn.execute("DELETE FROM bank_responses WHERE query_id=?", (qid,))
        conn.execute("DELETE FROM bank_queries WHERE query_id=?", (qid,))


@app.post("/api/scenes/delete")
async def delete_scene(request: Request):
    """删除业务场景（通用场景除外）：连同该场景下的题目与作答一并删除。"""
    body = await request.json()
    key = (body.get("key") or "").strip()
    custom = db.dj(_get_setting("custom_scenes"), []) or []
    conn = db.get_conn()
    ids = _scene_query_ids(conn, key)
    hit = next((c for c in custom if c["key"] == key), None)
    if not hit and not ids:
        return JSONResponse({"error": "场景不存在或已为空"}, status_code=404)
    _delete_bank_queries(conn, ids)
    conn.commit()
    if hit:
        _set_setting("custom_scenes", db.j([c for c in custom if c["key"] != key]))
    db.audit("demo-admin", "scene_delete", {"key": key, "questions_deleted": len(ids)})
    return {"ok": True, "deleted": len(ids)}


@app.get("/v1/bank/questions")
def bank_questions(scene: str):
    """场景详情：该场景下的题目清单（含打分状态）。"""
    conn = db.get_conn()
    out = []
    for r in conn.execute(
            "SELECT q.query_id, q.query_text, q.domain_tags, q.source, q.created_at, q.ideal, q.tenant_id, "
            "EXISTS(SELECT 1 FROM bank_responses b WHERE b.query_id=q.query_id) AS has_resp "
            "FROM bank_queries q WHERE (q.tenant_id IS NULL OR q.tenant_id=?) AND q.source!='reflow_staged' "
            "ORDER BY q.created_at DESC", (seed.TENANT,)).fetchall():
        if (db.dj(r["domain_tags"], ["general"]) or ["general"])[0] != scene:
            continue
        out.append({"query_id": r["query_id"], "query": r["query_text"], "source": r["source"],
                    "created_at": r["created_at"], "ideal": r["ideal"] or "", "scored": bool(r["has_resp"])})
        if len(out) >= 500:
            break
    return {"questions": out, "scene": scene}


@app.post("/v1/bank/question/delete")
async def bank_question_delete(request: Request):
    body = await request.json()
    qid = (body.get("query_id") or "").strip()
    conn = db.get_conn()
    row = conn.execute("SELECT query_id FROM bank_queries WHERE query_id=?", (qid,)).fetchone()
    if not row:
        return JSONResponse({"error": "题目不存在"}, status_code=404)
    _delete_bank_queries(conn, [qid])
    conn.commit()
    db.audit("demo-admin", "bank_question_delete", {"query_id": qid})
    return {"ok": True}


@app.post("/v1/bank/question/relabel")
async def bank_question_relabel(request: Request):
    """更改题目的场景标签。"""
    body = await request.json()
    qid = (body.get("query_id") or "").strip()
    domain = (body.get("domain") or "").strip()
    if not domain:
        return JSONResponse({"error": "缺少目标场景"}, status_code=422)
    conn = db.get_conn()
    row = conn.execute("SELECT query_id FROM bank_queries WHERE query_id=?", (qid,)).fetchone()
    if not row:
        return JSONResponse({"error": "题目不存在"}, status_code=404)
    conn.execute("UPDATE bank_queries SET domain_tags=? WHERE query_id=?", (db.j([domain]), qid))
    conn.commit()
    db.audit("demo-admin", "bank_question_relabel", {"query_id": qid, "domain": domain})
    return {"ok": True}


@app.get("/v1/bank/staged")
def bank_staged(domain: str = None):
    """待并入区：自动收集、尚未并入数据集的 query，供逐条审核。"""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT query_id, query_text, domain_tags, created_at FROM bank_queries "
        "WHERE tenant_id=? AND source='reflow_staged' ORDER BY created_at DESC", (seed.TENANT,)).fetchall()
    out = []
    for r in rows:
        dom = (db.dj(r["domain_tags"], ["general"]) or ["general"])[0]
        if domain and dom != domain:
            continue
        out.append({"query_id": r["query_id"], "query": r["query_text"], "domain": dom, "ts": r["created_at"]})
    return {"staged": out}


@app.post("/v1/bank/staged/commit")
async def bank_staged_commit(request: Request):
    """把选中的待并入 query 转正式入库（进入待评测）；未选中的保留在待并入区。"""
    body = await request.json()
    ids = body.get("query_ids") or []
    if not ids:
        return JSONResponse({"error": "没有选中任何条目"}, status_code=422)
    conn = db.get_conn()
    ph = ",".join("?" * len(ids))
    n = conn.execute(f"UPDATE bank_queries SET source='reflow' WHERE tenant_id=? AND source='reflow_staged' "
                     f"AND query_id IN ({ph})", (seed.TENANT, *ids)).rowcount
    conn.commit()
    db.audit("demo-admin", "bank_staged_commit", {"count": n})
    return {"committed": n}


@app.post("/v1/bank/staged/discard")
async def bank_staged_discard(request: Request):
    """剔除待并入区的脏数据（永久删除，不进数据集）。"""
    body = await request.json()
    ids = body.get("query_ids") or []
    if not ids:
        return JSONResponse({"error": "没有选中任何条目"}, status_code=422)
    conn = db.get_conn()
    ph = ",".join("?" * len(ids))
    n = conn.execute(f"DELETE FROM bank_queries WHERE tenant_id=? AND source='reflow_staged' "
                     f"AND query_id IN ({ph})", (seed.TENANT, *ids)).rowcount
    conn.commit()
    db.audit("demo-admin", "bank_staged_discard", {"count": n})
    return {"discarded": n}


@app.post("/v1/bank/import")
async def bank_import(request: Request):
    """标注数据导入（冷启动工具，§3.5）。items: [{query, domain, labels: {model_id: 0/1}}]"""
    body = await request.json()
    items = body.get("items") or []
    if len(items) > 5000:
        return JSONResponse({"error": f"单次最多导入 5000 条（当前 {len(items)} 条），请分批导入"}, status_code=422)
    tenant_id = body.get("tenant_id") or seed.TENANT
    conn = db.get_conn()
    n = 0
    skipped = 0
    existing = {row["query_text"].strip() for row in conn.execute(
        "SELECT query_text FROM bank_queries WHERE tenant_id=? OR tenant_id IS NULL", (tenant_id,)).fetchall()}
    for it in items:
        if not it.get("query"):
            continue
        if it["query"].strip() in existing:
            skipped += 1
            continue
        existing.add(it["query"].strip())
        qid = "imp-" + db.new_id()[:8]
        conn.execute(
            "INSERT INTO bank_queries (query_id, tenant_id, embedding, text_ref, query_text, domain_tags, "
            "created_at, ttl_days, source, ideal) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (qid, tenant_id, db.j(embeddings.embed(it["query"])), f"vault://import/{qid}", it["query"],
             db.j([it.get("domain", "general")]), db.now_ts(), 365, "tenant_imported",
             (it.get("ideal") or "").strip() or None))
        for mid, val in (it.get("labels") or {}).items():
            conn.execute(
                "INSERT INTO bank_responses (query_id, model_id, response_embedding, completion_tokens, "
                "label_value, label_confidence, label_source, label_kind, created_at, updated_at) "
                "VALUES (?,?,NULL,0,?,1.0,'ground_truth','capability',?,?)",
                (qid, mid, float(val), db.now_ts(), db.now_ts()))
        n += 1
    conn.commit()
    db.audit("demo-admin", "bank_import", {"count": n, "skipped": skipped, "tenant_id": tenant_id})
    return {"imported": n, "skipped": skipped}


# ============ 路由画像（Avengers-Pro 产品化：簇 × 模型 分数矩阵） ============

_profile_task = {"status": "idle", "done": 0, "total": 0, "version": 1}


@app.get("/api/profile")
def routing_profile(tenant_id: str = None, alpha: float = Query(0.7, ge=0.0, le=1.0), policy_id: str = None):
    """路由画像：按问题簇（领域）× 模型 输出 性能分 / 效率分 / α 综合分。
    性能分来自评测数据集（bank）的历史命中率；效率分由模型单价归一化。
    综合分 = α·性能 + (1-α)·效率 —— α 是管理员可调的质量-成本旋钮。"""
    conn = db.get_conn()
    tid = tenant_id or seed.TENANT
    models = {r["model_id"]: dict(r) for r in conn.execute(
        "SELECT model_id, display_name, price_input, price_output, is_default FROM models WHERE status='active'")}
    # 按策略过滤候选模型 + 取聚合参数（不同策略 → 不同去向）
    pol = None
    if policy_id:
        prow = conn.execute("SELECT * FROM policies WHERE policy_id=?", (policy_id,)).fetchone()
        if prow:
            pol = dict(prow)
            wl = db.dj(pol.get("model_whitelist"), []) or []
            if wl:
                models = {k: v for k, v in models.items() if k in wl}
    if not models:
        return {"clusters": [], "alpha": alpha, "policy_id": policy_id}
    prices = {mid: m["price_input"] + m["price_output"] for mid, m in models.items()}
    inv = {mid: 1.0 / max(0.01, p) for mid, p in prices.items()}
    lo, hi = min(inv.values()), max(inv.values())
    eff = {mid: round((v - lo) / (hi - lo), 3) if hi > lo else 0.5 for mid, v in inv.items()}

    stats = {}   # domain -> model -> [sum, cnt]
    counts = {}  # domain -> query count
    for r in conn.execute(
            "SELECT bq.domain_tags, bq.query_id, br.model_id, br.label_value, br.label_confidence "
            "FROM bank_queries bq JOIN bank_responses br ON bq.query_id=br.query_id "
            "WHERE (bq.tenant_id IS NULL OR bq.tenant_id=?) AND br.label_kind='capability'", (tid,)):
        domains = db.dj(r["domain_tags"], []) or ["general"]
        for d in domains:
            counts.setdefault(d, set()).add(r["query_id"])
            if r["model_id"] not in models or r["label_value"] is None:
                continue
            cell = stats.setdefault(d, {}).setdefault(r["model_id"], [0.0, 0])
            cell[0] += r["label_value"] * (r["label_confidence"] or 0.5)
            cell[1] += 1

    clusters = []
    for d, per_model in sorted(stats.items(), key=lambda x: -len(counts.get(x[0], set()))):
        scores = {}
        for mid in models:
            cell = per_model.get(mid)
            perf = round(cell[0] / cell[1], 3) if cell and cell[1] else None
            combined = round(alpha * perf + (1 - alpha) * eff[mid], 3) if perf is not None else None
            scores[mid] = {"perf": perf, "eff": eff[mid], "combined": combined}
        valid = {m: s["combined"] for m, s in scores.items() if s["combined"] is not None}
        best = max(valid, key=valid.get) if valid else None
        agg_with = None
        if pol and pol.get("allow_aggregation") and len(valid) >= 2:
            ranked2 = sorted(valid.items(), key=lambda x: -x[1])
            t_val = (db.dj(pol.get("params"), {}) or {}).get("t", 0.8)
            # 聚合值越低越容易聚合：两名分差小于阈值时该场景走「聚合」
            if ranked2[0][1] - ranked2[1][1] < max(0.02, (1 - float(t_val)) * 0.3):
                agg_with = ranked2[1][0]
        clusters.append({"domain": d, "queries": len(counts.get(d, set())),
                         "scores": scores, "best": best, "agg_with": agg_with})
    return {"clusters": clusters, "alpha": alpha, "version": _profile_task["version"],
            "models": {mid: m["display_name"] for mid, m in models.items()}}


@app.post("/api/profile/rebuild")  # running 态重复触发直接拒绝（见函数体守门）
async def profile_rebuild():
    """重建路由画像（离线管道：向量化 → 聚类 → 回填评测 → 画像矩阵）。演示环境为模拟进度。"""
    if _profile_task["status"] == "running":
        return {"task": _profile_task}
    conn = db.get_conn()
    n_domains = conn.execute("SELECT COUNT(DISTINCT domain_tags) AS c FROM bank_queries").fetchone()["c"]
    n_models = conn.execute("SELECT COUNT(*) AS c FROM models WHERE status='active'").fetchone()["c"]
    _profile_task.update({"status": "running", "done": 0, "total": max(1, n_domains * n_models)})
    asyncio.create_task(_run_profile_rebuild())
    return {"task": _profile_task}


@app.get("/api/profile/rebuild/status")
def profile_rebuild_status():
    return {"task": _profile_task}


async def _run_profile_rebuild():
    for i in range(_profile_task["total"]):
        _profile_task["done"] = i + 1
        await asyncio.sleep(0.06)
    _profile_task["status"] = "completed"
    _profile_task["version"] += 1
    db.audit("demo-admin", "profile_rebuild", {"version": _profile_task["version"]})


# ============ 明细导出（标书 F-5-05：CSV 导出与查询结果一致） ============

@app.get("/api/export/traces.csv")
def export_traces_csv(days: int = Query(30, ge=1, le=90), mode: str = None,
                      model: str = None, status: str = None):
    import csv
    import io
    from fastapi.responses import Response
    conn = db.get_conn()
    since = time.time() - days * 86400
    decision_by_trace = {}
    for r in conn.execute("SELECT rd.trace_id, rd.decision FROM route_decisions rd "
                          "JOIN traces t ON rd.trace_id=t.trace_id WHERE t.ts>?", (since,)):
        decision_by_trace[r["trace_id"]] = db.dj(r["decision"], {})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["trace_id", "time", "mode", "path", "final_model", "cost_usd", "latency_ms",
                "status", "is_explore", "query_masked"])
    for t in conn.execute("SELECT * FROM traces WHERE ts>? ORDER BY ts DESC", (since,)):
        d = decision_by_trace.get(t["trace_id"]) or {}
        t_mode = d.get("mode") or "auto"
        if mode and t_mode != mode:
            continue
        if model and t["final_model"] != model:
            continue
        if status and (t["status"] or "ok") != status:
            continue
        w.writerow([t["trace_id"], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t["ts"])),
                    t_mode, t["switch_result"], t["final_model"], t["total_cost"],
                    t["total_latency_ms"], t["status"], t["is_explore"],
                    traces.mask_text(t["query_text"] or "")])
    db.audit("demo-admin", "export_csv", {"kind": "traces", "days": days, "mode": mode, "model": model})
    return Response(content="﻿" + buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=traces.csv"})


@app.get("/api/export/responses.csv")
def export_responses_csv(card_id: str = None, days: int = Query(30, ge=1, le=90)):
    import csv
    import io
    from fastapi.responses import Response
    conn = db.get_conn()
    since = time.time() - days * 86400
    where = "event_type='card_submitted' AND admitted=1 AND COALESCE(channel,'')!='test' AND ts>?"
    args = [since]
    if card_id:
        where += " AND json_extract(card,'$.card_id')=?"
        args.append(card_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time", "card_id", "component_type", "user_pseudonym", "selection", "modified_from_default"])
    for r in conn.execute(f"SELECT * FROM events WHERE {where} ORDER BY ts DESC", args):
        card = db.dj(r["card"], {})
        p = db.dj(r["payload"], {})
        sel = p.get("user_selection")
        sel_out = db.j(sel) if isinstance(sel, (list, dict)) else sel
        if isinstance(sel_out, str) and sel_out[:1] in ("=", "+", "-", "@"):
            sel_out = "'" + sel_out  # 防 CSV 公式注入
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])),
                    card.get("card_id"), card.get("component_type"), r["user_id"],
                    sel_out, p.get("modified_from_default")])
    db.audit("demo-admin", "export_csv", {"kind": "responses", "card_id": card_id, "days": days})
    return Response(content="﻿" + buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=responses.csv"})


# ============ P3 看板与 Trace ============

@app.get("/api/dashboard/overview")
def api_overview(days: int = Query(7, ge=1, le=90), mode: str = None,
                 model: str = None, status: str = None):
    return dashboard.overview(days, mode=mode or None, model=model or None, status=status or None)


@app.get("/api/dashboard/insights")
def api_insights(days: int = Query(30, ge=1, le=90)):
    return dashboard.insights(days)


@app.get("/api/ab/compare")
def api_ab_compare(days: int = Query(14, ge=1, le=90)):
    return dashboard.ab_compare(days)


@app.get("/api/traces")
def api_traces(switch_result: str = None, status: str = None, min_cost: float = None,
               min_latency: int = None, is_explore: bool = False, limit: int = 50,
               offset: int = 0, mode: str = None, final_model: str = None, days: int = None):
    since = (time.time() - days * 86400) if days else None
    return {"traces": traces.list_traces({
        "switch_result": switch_result, "status": status, "min_cost": min_cost,
        "min_latency": min_latency, "is_explore": is_explore, "offset": offset,
        "mode": mode, "final_model": final_model, "since": since}, min(limit, 1000))}


@app.get("/api/traces/{trace_id}")
def api_trace_detail(trace_id: str, unmask: bool = False):
    t = traces.get_trace(trace_id, unmask=unmask)
    if not t:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return t


@app.get("/api/audit")
def api_audit(limit: int = 50):
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    for r in rows:
        r["detail"] = db.dj(r["detail"], {})
    return {"audit": rows}


# ============ 品牌与静态资源 ============

ACTIVE_BRAND_FILE = os.path.join(BASE, "brand", "active.json")


@app.get("/api/brands/active")
def get_active_brand():
    """租户级生效风格：所有管理端与嵌入端统一读取，不再依赖浏览器本地记录。"""
    try:
        with open(ACTIVE_BRAND_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"file": "brand-tokens.default.json"}


@app.post("/api/brands/active")
async def set_active_brand(request: Request):
    body = await request.json()
    fn = body.get("file") or ""
    if not os.path.exists(os.path.join(BASE, "brand", fn)) or not fn.startswith("brand-tokens."):
        return JSONResponse({"error": "风格文件不存在"}, status_code=422)
    with open(ACTIVE_BRAND_FILE, "w", encoding="utf-8") as f:
        json.dump({"file": fn}, f)
    db.audit("demo-admin", "brand_activate", {"file": fn})
    return {"ok": True, "file": fn}


@app.post("/api/brands/delete")
async def delete_brand(request: Request):
    body = await request.json()
    fn = body.get("file") or ""
    if fn == "brand-tokens.default.json":
        return JSONResponse({"error": "默认风格不可删除"}, status_code=409)
    try:
        with open(ACTIVE_BRAND_FILE, encoding="utf-8") as f:
            active = json.load(f).get("file")
    except (OSError, ValueError):
        active = "brand-tokens.default.json"
    if fn == active:
        # 删除生效中的风格：先回退到默认，再删除
        with open(ACTIVE_BRAND_FILE, "w", encoding="utf-8") as f:
            json.dump({"file": "brand-tokens.default.json"}, f)
    path = os.path.join(BASE, "brand", fn)
    if not os.path.exists(path) or not fn.startswith("brand-tokens."):
        return JSONResponse({"error": "风格文件不存在"}, status_code=404)
    os.remove(path)
    db.audit("demo-admin", "brand_delete", {"file": fn})
    return {"ok": True}


@app.get("/api/brands")
def list_brands():
    brand_dir = os.path.join(BASE, "brand")
    out = []
    for fn in sorted(os.listdir(brand_dir)):
        if fn.startswith("brand-tokens."):
            with open(os.path.join(brand_dir, fn), encoding="utf-8") as f:
                data = json.load(f)
            out.append({"file": fn, "brand_id": data.get("brand_id"), "brand_name": data.get("brand_name")})
    return {"brands": out}


@app.post("/api/brands")
async def save_brand(request: Request):
    """导入品牌 design token：校验 → 落盘为 brand-tokens.<brand_id>.json → 立即可在品牌切换中选用。"""
    import re as _re
    body = await request.json()
    tokens = body.get("tokens")
    brand_id = (body.get("brand_id") or (tokens or {}).get("brand_id") or "").strip().lower()
    brand_name = (body.get("brand_name") or (tokens or {}).get("brand_name") or "").strip()
    if not isinstance(tokens, dict):
        return JSONResponse({"error": "tokens 必须是 JSON 对象"}, status_code=422)
    if not _re.fullmatch(r"[a-z0-9][a-z0-9-]{1,23}", brand_id):
        return JSONResponse({"error": "brand_id 需为 2-24 位小写字母、数字或短横线"}, status_code=422)
    if brand_id == "default":
        return JSONResponse({"error": "默认品牌不可覆盖，请换一个 brand_id"}, status_code=409)
    if not brand_name:
        return JSONResponse({"error": "缺少 brand_name"}, status_code=422)
    color = tokens.get("color") or {}
    missing = [k for k in ("primary", "bg_page", "bg_surface", "text_primary") if not color.get(k)]
    if missing:
        return JSONResponse({"error": f"color 缺少必需项：{', '.join(missing)}"}, status_code=422)
    bad = [f"color.{k}" for k, v in color.items()
           if not isinstance(v, str) or not _re.fullmatch(r"#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)", v.strip())]
    if bad:
        return JSONResponse({"error": f"颜色值格式不合法：{', '.join(bad[:5])}"}, status_code=422)
    tokens = {**tokens, "brand_id": brand_id, "brand_name": brand_name}
    fn = f"brand-tokens.{brand_id}.json"
    with open(os.path.join(BASE, "brand", fn), "w", encoding="utf-8") as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)
    db.audit("demo-admin", "brand_import", {"brand_id": brand_id, "brand_name": brand_name})
    return {"ok": True, "file": fn, "brand_id": brand_id, "brand_name": brand_name}


app.mount("/brand", StaticFiles(directory=os.path.join(BASE, "brand")), name="brand")
app.mount("/contracts", StaticFiles(directory=os.path.join(BASE, "contracts")), name="contracts")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "web", "index.html"))


app.mount("/web", StaticFiles(directory=os.path.join(BASE, "web"), html=True), name="web")
