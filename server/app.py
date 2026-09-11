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

from . import cards, dashboard, db, embeddings, events, mockmodels, registry, router_core, seed, traces

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
        _hit = None
        for _r in db.get_conn().execute("SELECT policy_id FROM policies WHERE enabled=1").fetchall():
            if _policy_api_key(_r["policy_id"]) == body["api_key"]:
                _hit = _r["policy_id"]
                break
        if not _hit:
            # Key 无效（拼错 / 已被重置 / 策略停用）：明确报错，不静默回落默认策略——否则计费与归因全错、调用方毫无感知
            await emit({"step": "final", "trace_id": trace_id, "error": "invalid_api_key",
                        "content": "API Key 无效或已被重置：请在「路由策略」页复制当前 Key 并更新调用方配置。"})
            return
        body["policy_id"] = _hit
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
        if hit and hit.get("semantic_category") == "present":
            # v2 展示类组件：模型判定用它「翻译」结构化内容——带数据直接渲染，无提交、不等待用户
            v2t = registry.V2_TYPE_MAP.get(hit["component_type"], "table")
            params = mockmodels.gen_present_params(v2t, text)
            ct_out = hit["component_type"]
            envelope = _envelope(ct_out, "model_tool_call", params,
                                 card={"card_id": hit["card_id"], "version": hit["version"]})
            if hit.get("style_overrides"):
                envelope["style_overrides"] = hit["style_overrides"]
            recorder.span("card_render", {"card_id": hit["card_id"], "card_version": hit["version"],
                                          "component_type": ct_out, "trigger_source": "model_tool_call",
                                          "degraded": False, "competitors": competitors[:3]})
            recorder.finish("fastlane", None, 0.0002, 120, False)
            _pn = registry.V2_META.get(v2t, {}).get("label", "内容")
            await emit({"step": "final", "trace_id": trace_id, "turn_id": turn_id,
                        "content": f"已为你整理为{_pn}" + (f"：{params['title']}" if params.get("title") else "。"),
                        "components": [envelope]})
            return
        if hit:
            envelope, degraded = _build_ask_envelope(hit, text)
            if hit.get("style_overrides"):
                envelope["style_overrides"] = hit["style_overrides"]
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
    # v5.0 请求级聚合参数：客户页面把终端用户的「聚合答案」选项透传进来，按次覆盖策略默认
    agg_req = str(body.get("aggregate") or "auto").lower()
    if agg_req not in ("on", "off", "auto"):
        agg_req = "auto"
    agg_override_denied = False
    if agg_req == "on" and mode == "auto":
        _pp = db.dj(policy.get("params"), {}) if isinstance(policy.get("params"), str) else (policy.get("params") or {})
        if not _pp.get("allow_agg_override", 1):
            # 管理员在策略里关闭了「允许调用方按次覆盖」：聚合默认值是硬约束，成本不可被调用方放大
            agg_override_denied = True
        else:
            # 终端用户点了「聚合答案」：允许并尽量兑现聚合（跳过快车道、细排至少保留两份）
            req["policy"]["allow_aggregation"] = 1
            req["policy"]["force_agg"] = 1
            if req["policy"].get("latency_tier") == "fast":
                req["policy"]["latency_tier"] = "balanced"
    elif agg_req == "off":
        req["policy"]["allow_aggregation"] = 0
    if mode == "multi":
        req["policy"]["allow_aggregation"] = 1
    if degrade_by_quota:
        req["policy"]["latency_tier"] = "fast"
        req["policy"]["allow_aggregation"] = 0
        req["policy"].pop("force_agg", None)  # 配额降级优先于请求级 aggregate=on，否则预算帽可被绕过
        req["mode"] = "auto" if mode == "multi" else req["mode"]

    result = await router_core.run_route(req, recorder, emit)
    if result.get("error") and not result.get("final"):
        await emit({"step": "final", "trace_id": trace_id, "error": result["error"],
                    "content": "所有候选模型均不可用，请稍后重试。"})
        return

    final = result["final"]
    decision = result["decision"]

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
        "request_id": trace_id,
        "content": final["content"], "components": components,
        "applied": {"aggregate": agg_req, "aggregation_used": decision["switch_result"] == "aggregated",
                    "override_allowed": not agg_override_denied,
                    "policy_id": policy.get("policy_id")},
        "decision_summary": {
            "mode": decision.get("mode", "auto"),
            "switch_result": decision["switch_result"],
            "route_layer": decision.get("route_layer"),
            "dimensions": decision.get("dimensions") or [],
            "dim_scores": decision.get("dim_scores") or {},
            "aggregate_override": agg_req if agg_req != "auto" else None,
            "aggregate_override_denied": agg_override_denied,
            "final_model": decision["final_model_or_aggregator"],
            "candidates": decision["candidate_models"],
            "aggregator": decision.get("aggregator_model"),
            "total_cost": decision["total_cost"],
            "total_latency_ms": decision["total_latency_ms"],
            "model_calls": decision.get("model_calls") or [],
            "policy": {
                "policy_id": policy.get("policy_id"),
                "name": policy.get("name"),
                "latency_tier": policy.get("latency_tier"),
                "allow_aggregation": policy.get("allow_aggregation"),
                "K": (db.dj(policy.get("params"), {}) if isinstance(policy.get("params"), str)
                      else (policy.get("params") or {})).get("K"),
                "alpha": (db.dj(policy.get("params"), {}) if isinstance(policy.get("params"), str)
                          else (policy.get("params") or {})).get("alpha", 0.7),
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
def embed_envelope(card_id: str, key: str = None):
    """植入 SDK：按配置 ID 获取可渲染的协议信封（sia.js 用）。仅已上线配置可被植入。
    v2：必须携带产品接入 Key（此前完全不校验——任何人可拉任意组件配置）。"""
    conn = db.get_conn()
    # 鉴权到具体产品：key 只能读它自己产品下的组件（此前任意产品 key 可拉全部组件配置）
    prod = next((r for r in conn.execute("SELECT product_id, card_ids FROM products").fetchall()
                 if key and (_pub_key(r["product_id"]) == key or _mcp_key(r["product_id"]) == key)), None)
    if not prod:
        return JSONResponse({"error": "invalid_key", "message": "请携带产品接入 Key（产品管理页可复制）"}, status_code=401)
    if card_id not in db.dj(prod["card_ids"], []):
        return JSONResponse({"error": "forbidden", "message": "该组件不属于此产品"}, status_code=403)
    card = cards.get_card(card_id)
    if not card:
        return JSONResponse({"error": "配置不存在"}, status_code=404)
    if not (card.get("status") == "published" or (card.get("status") == "draft" and (card.get("version") or 0) >= 1)):
        return JSONResponse({"error": "配置未上线，不能植入"}, status_code=409)
    envelope, degraded = _build_ask_envelope(card, "")
    if card.get("style_overrides"):
        envelope["style_overrides"] = card["style_overrides"]
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
    if ct == "feedback.preference" and not config.get("candidates"):
        # v2：候选由模型按对话给出（params.candidates）——演示生成两份方案
        params["candidates"] = [
            {"alias": "方案 A", "label": "方案 A", "content": "优先保证时效：改走直达线路，成本上浮约 8%。"},
            {"alias": "方案 B", "label": "方案 B", "content": "优先控制成本：维持现有线路，预计多用 2 天。"}]
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


@app.post("/v1/events")
async def ingest_events(request: Request):
    body = await request.json()
    evts = body.get("events") if isinstance(body, dict) and "events" in body else [body]
    results = [events.ingest(e) for e in evts]
    return {"results": results}


# ============ 群体决策（服务端串行化，§2.4） ============

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
        out.append(m)
    return {"models": out}


# ---------- 对外接入：API Key 管理（明文仅创建时返回一次，库中只存哈希） ----------

def _mcp_key(product_id: str) -> str:
    import hashlib as _h
    salts = db.dj(_get_setting("product_key_salt"), {}) or {}
    n = int(salts.get(product_id, 0))
    seed_s = "mcp:" + product_id + ("" if n == 0 else f":{n}")
    return "sk-mcp-" + _h.md5(seed_s.encode()).hexdigest()[:18]


def _pub_key(product_id: str) -> str:
    """前端公开 Key：只用于 envelope / sia.css / 事件回传（页面源码可见，泄露不暴露注册表全量配置）。
    与私有 Key 共用同一份盐：重置一次两把 Key 同时轮换。"""
    import hashlib as _h
    salts = db.dj(_get_setting("product_key_salt"), {}) or {}
    n = int(salts.get(product_id, 0))
    seed_s = "pub:" + product_id + ("" if n == 0 else f":{n}")
    return "pk-web-" + _h.md5(seed_s.encode()).hexdigest()[:18]


@app.post("/api/products/{product_id}/reset-key")
async def reset_product_key(product_id: str):
    """重置产品接入 Key：旧 Key 立即失效（注册表与信封接口都将拒绝）。"""
    conn = db.get_conn()
    row = conn.execute("SELECT name FROM products WHERE product_id=?", (product_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    salts = db.dj(_get_setting("product_key_salt"), {}) or {}
    salts[product_id] = int(salts.get(product_id, 0)) + 1
    _set_setting("product_key_salt", db.j(salts))
    db.audit("demo-admin", "product_key_reset", {"product_id": product_id, "name": row["name"]})
    return {"ok": True, "mcp_key": _mcp_key(product_id), "pub_key": _pub_key(product_id)}


@app.get("/v1/products/{product_id}/sia.css")
def product_sia_css(product_id: str, key: str = None):
    """SDK 三件套之 CSS：按产品风格主题 token 实时生成（兑现「风格主题出包固化」）。"""
    from fastapi.responses import PlainTextResponse
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM products WHERE product_id=?", (product_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "product_not_found"}, status_code=404)
    if not key or key not in (_pub_key(product_id), _mcp_key(product_id)):
        return JSONResponse({"error": "invalid_key"}, status_code=401)
    import json as _json
    _bf = os.path.basename(row["brand_file"] or "brand-tokens.default.json")
    brand_path = os.path.join(BASE, "brand", _bf)
    try:
        tk = _json.load(open(brand_path, encoding="utf-8"))
    except Exception:
        tk = {}
    col = tk.get("color", {})
    rad = tk.get("radius", {})
    fnt = tk.get("font", {})
    css_vars = {
        "--primary": col.get("primary"), "--primary-weak": col.get("primary_weak"),
        "--brand-accent": col.get("accent") or col.get("primary"), "--brand-ring": col.get("ring") or col.get("primary"),
        "--bg-page": col.get("bg_page"), "--bg-surface": col.get("bg_surface"),
        "--text-primary": col.get("text_primary"), "--text-secondary": col.get("text_secondary"),
        "--text-muted": col.get("text_muted"), "--border": col.get("border"),
        "--success": col.get("success"), "--warning": col.get("warning"), "--danger": col.get("danger"),
        "--radius-card": rad.get("card"), "--radius-control": rad.get("control"),
        "--font-family": fnt.get("family"), "--font-base": fnt.get("size_base"),
        "--font-weight-base": fnt.get("weight_base"),
        "--brand-shadow": tk.get("shadow"),
    }
    decls = "".join(f"{k}:{v};" for k, v in css_vars.items() if v)
    dk = tk.get("color_dark") or {}
    dark_vars = {
        "--primary": dk.get("primary"), "--primary-weak": dk.get("primary_weak"),
        "--brand-accent": dk.get("accent"), "--brand-ring": dk.get("ring"),
        "--bg-page": dk.get("bg_page"), "--bg-surface": dk.get("bg_surface"),
        "--text-primary": dk.get("text_primary"), "--text-secondary": dk.get("text_secondary"),
        "--text-muted": dk.get("text_muted"), "--border": dk.get("border"),
        "--success": dk.get("success"), "--warning": dk.get("warning"), "--danger": dk.get("danger"),
    }
    dark_decls = "".join(f"{k}:{v};" for k, v in dark_vars.items() if v)
    dark_block = ("@media (prefers-color-scheme: dark){.brand-scope{%s}}\n" % dark_decls) if dark_decls else ""
    body = ("/* Smart Interaction SDK CSS — 产品「%s」的风格主题 token（出包固化：平台改风格后需重新拉取部署） */\n"
            ".brand-scope{%s}\n%ssia-card{display:block;}\n") % (row["name"], decls, dark_block)
    return PlainTextResponse(body, media_type="text/css")



def _product_row(r):
    cur_hash = registry.build_registry(dict(r)).get("content_hash")
    keys = r.keys()
    pulled_hash = r["pulled_hash"] if "pulled_hash" in keys else None
    pulled_at = r["pulled_at"] if "pulled_at" in keys else None
    return {"product_id": r["product_id"], "name": r["name"], "brand_file": r["brand_file"],
            "card_ids": db.dj(r["card_ids"], []), "created_at": r["created_at"],
            "mcp_key": _mcp_key(r["product_id"]), "pub_key": _pub_key(r["product_id"]),
            "current_hash": cur_hash, "pulled_hash": pulled_hash, "pulled_at": pulled_at,
            "stale": bool(pulled_hash) and pulled_hash != cur_hash}


@app.get("/v1/products/{product_id}/registry")
def product_registry(product_id: str, key: str = None):
    """组件注册表（v2）：agent 后端凭产品 SDK Key 拉取，作为工具声明给大模型。
    只含已发布且属于 v2 注册集的组件实例；说明 + 参数 schema + 提交结构，UI 排布不暴露。"""
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM products WHERE product_id=?", (product_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "product_not_found"}, status_code=404)
    if not key or key != _mcp_key(product_id):
        return JSONResponse({"error": "invalid_key", "message": "请携带后端私有 Key（接入配置页可复制）"}, status_code=401)
    reg = registry.build_registry(dict(row))
    # 拉取注册表 ≈ 出包动作：记录指纹，平台据此提示「线上包是否落后」
    try:
        conn.execute("UPDATE products SET pulled_hash=?, pulled_at=? WHERE product_id=?",
                     (reg.get("content_hash"), db.now_ts(), product_id))
        conn.commit()
    except Exception:
        pass
    return reg


@app.post("/api/components/schema-preview")
async def components_schema_preview(request: Request):
    """编辑器的数据 schema 预览：按当前配置实时生成注册 schema（与正式注册表同源）。"""
    body = await request.json()
    stub = {"component_type": body.get("component_type") or "",
            "field_bindings": {"config": body.get("config") or {}}}
    return {"params_schema": registry.params_schema_for(stub),
            "fixed": registry.fixed_config_for(stub),
            "submit_schema": registry.submit_schema_for(stub)}


@app.get("/api/components/catalog")
def components_catalog():
    """v2 组件库目录：交互 5 + 展示 2，含参数 schema 与提交结构（数据 schema 展现）。"""
    return {"catalog": registry.build_catalog()}


@app.get("/api/products")
def list_products():
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM products ORDER BY created_at").fetchall()
    return {"products": [_product_row(r) for r in rows]}


@app.post("/api/products")
async def create_product(request: Request):
    """新建产品：名称 + 风格主题（单选） + 绑定组件（多选）。每个产品一个 MCP 接入点。"""
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
    if conn.execute("SELECT 1 FROM products WHERE name=? AND product_id!=?", (name, product_id)).fetchone():
        return JSONResponse({"error": "已有同名产品"}, status_code=409)
    brand_file = (body.get("brand_file") or row["brand_file"]).strip()
    card_ids = body.get("card_ids")
    if isinstance(card_ids, list):
        valid = {r["card_id"] for r in conn.execute("SELECT card_id FROM cards").fetchall()}
        card_ids = db.j([c for c in card_ids if isinstance(c, str) and c in valid])
    else:
        card_ids = row["card_ids"]
    # brand_file 只允许品牌目录内的文件名（防路径注入；必须真实存在）
    _bf = body.get("brand_file")
    if _bf is not None:
        import os as _os
        if _os.path.basename(str(_bf)) != str(_bf) or not str(_bf).endswith(".json") or not _os.path.exists(
                _os.path.join(BASE, "brand", str(_bf))):
            return JSONResponse({"error": "风格主题文件不合法"}, status_code=422)
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
    salts = db.dj(_get_setting("policy_key_salt"), {}) or {}
    n = int(salts.get(policy_id, 0))
    seed_s = "route-key:" + policy_id + ("" if n == 0 else f":{n}")
    return "sk-route-" + _h.md5(seed_s.encode()).hexdigest()[:16]


@app.post("/v1/policies/{policy_id}/reset-key")
async def reset_policy_key(policy_id: str):
    """重置策略 API Key：旧 Key 立即失效（无效 Key 的调用被明确拒绝，不再静默回落默认策略）。"""
    conn = db.get_conn()
    row = conn.execute("SELECT name FROM policies WHERE policy_id=?", (policy_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    salts = db.dj(_get_setting("policy_key_salt"), {}) or {}
    salts[policy_id] = int(salts.get(policy_id, 0)) + 1
    _set_setting("policy_key_salt", db.j(salts))
    db.audit("demo-admin", "policy_key_reset", {"policy_id": policy_id, "name": row["name"]})
    return {"ok": True, "api_key": _policy_api_key(policy_id)}


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
    sets, vals = ["display_name=?"], [name]
    if body.get("provider") is not None:
        prov = str(body["provider"]).strip()
        if not (1 <= len(prov) <= 20):
            return JSONResponse({"error": "供应商必填，不超过 20 字"}, status_code=422)
        sets.append("provider=?")
        vals.append(prov)
    n = conn.execute(f"UPDATE models SET {', '.join(sets)} WHERE model_id=?", (*vals, model_id)).rowcount
    conn.commit()
    if not n:
        return JSONResponse({"error": "模型不存在"}, status_code=404)
    db.audit("demo-admin", "model_rename", {"model_id": model_id, "display_name": name})
    return {"ok": True}


@app.post("/v1/models/{model_id}/delete")
async def delete_model(model_id: str):
    """删除模型：默认兜底模型不可删；历史评测得分与调用记录保留用于审计。"""
    conn = db.get_conn()
    row = conn.execute("SELECT is_default, display_name FROM models WHERE model_id=?", (model_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "模型不存在"}, status_code=404)
    if row["is_default"]:
        return JSONResponse({"error": "默认兜底模型不能删除，请先把默认切换到其他模型"}, status_code=409)
    conn.execute("DELETE FROM models WHERE model_id=?", (model_id,))
    # 清理策略引用：候选白名单剔除该模型；策略兜底指向它时重置为平台默认兜底
    default_row = conn.execute("SELECT model_id FROM models WHERE is_default=1").fetchone()
    default_id = default_row["model_id"] if default_row else None
    n_wl = n_fb = 0
    for pr in conn.execute("SELECT policy_id, params, model_whitelist FROM policies").fetchall():
        wl = db.dj(pr["model_whitelist"], []) or []
        prm = db.dj(pr["params"], {}) or {}
        changed = False
        if model_id in wl:
            wl = [x for x in wl if x != model_id]
            changed = True; n_wl += 1
        if prm.get("fallback_model") == model_id:
            prm["fallback_model"] = default_id
            changed = True; n_fb += 1
        if changed:
            conn.execute("UPDATE policies SET model_whitelist=?, params=? WHERE policy_id=?",
                         (db.j(wl), db.j(prm), pr["policy_id"]))
    conn.commit()
    db.audit("demo-admin", "model_delete", {"model_id": model_id, "display_name": row["display_name"],
                                            "whitelist_cleaned": n_wl, "fallback_reset": n_fb})
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
    if not (0 <= pin <= 10000) or not (0 <= pout <= 10000):
        return JSONResponse({"error": "单价需在 0 ~ 10000 之间"}, status_code=422)
    if not (0 <= ctx <= 100000 * 1024):
        return JSONResponse({"error": "上下文长度超出合理范围"}, status_code=422)
    caps = db.dj(row["capabilities"], {}) or {}
    if ctx > 0:
        caps["context_window"] = ctx
    if body.get("vision") is not None:
        caps["vision"] = bool(body["vision"])  # 多模态（图像识别）能力：硬规则层按它分流
    # 成本双类型：自有部署按卡数做简易估算（GPU 卡·时折算），API 接入按官网单价
    deploy_type = (body.get("deploy_type") or row["deploy_type"] or "api").strip()
    if deploy_type not in ("api", "self_hosted"):
        return JSONResponse({"error": "接入方式仅支持 api / self_hosted"}, status_code=422)
    gpu_count = row["gpu_count"] or 0
    if deploy_type == "self_hosted":
        try:
            gpu_count = int(body.get("gpu_count") if body.get("gpu_count") is not None else gpu_count)
        except (TypeError, ValueError):
            return JSONResponse({"error": "卡数需为整数"}, status_code=422)
        if not (1 <= gpu_count <= 512):
            return JSONResponse({"error": "自有部署需填写卡数（1-512）"}, status_code=422)
        if not pin and not pout:
            # 简易估算：卡越多的模型单位吞吐成本越高（按 GPU 卡·时折算到每百万 token）
            pin = round(0.15 * gpu_count, 2)
            pout = round(0.30 * gpu_count, 2)
    else:
        gpu_count = 0
    conn.execute("UPDATE models SET price_input=?, price_output=?, capabilities=?, deploy_type=?, gpu_count=? "
                 "WHERE model_id=?", (pin, pout, db.j(caps), deploy_type, gpu_count, model_id))
    conn.commit()
    db.audit("demo-admin", "model_profile_data", {"model_id": model_id, "price_input": pin, "price_output": pout,
                                                  "context_window": ctx, "deploy_type": deploy_type, "gpu_count": gpu_count})
    return {"ok": True, "price_input": pin, "price_output": pout}


@app.post("/v1/models")
async def register_model(request: Request):
    body = await request.json()
    required = ["model_name", "display_name", "provider", "endpoint", "credential_ref"]
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
    import re as _re4
    provider = str(body["provider"]).strip()
    model_name = str(body["model_name"]).strip()
    if not (1 <= len(provider) <= 20):
        return JSONResponse({"error": "供应商必填，不超过 20 字"}, status_code=422)
    if not _re4.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}", model_name):
        return JSONResponse({"error": "模型型号需为 1-40 位字母、数字、点、下划线或短横线"}, status_code=422)
    # 同一型号允许多条接入（官方 API / 自有部署各一条）：实例 ID 由服务端生成，型号只是标签
    same_name = conn.execute("SELECT COUNT(*) c FROM models WHERE model_name=?", (model_name,)).fetchone()["c"]
    slug = _re4.sub(r"[^a-z0-9]+", "-", model_name.lower()).strip("-")[:18] or "model"
    model_id = None
    for _ in range(8):
        cand = slug + "-" + db.new_id()[:4]
        if not conn.execute("SELECT 1 FROM models WHERE model_id=?", (cand,)).fetchone():
            model_id = cand
            break
    if not model_id:
        return JSONResponse({"error": "实例 ID 生成失败，请重试"}, status_code=500)
    body["model_id"] = model_id
    deploy_type = (body.get("deploy_type") or "api").strip()
    if deploy_type not in ("api", "self_hosted"):
        return JSONResponse({"error": "接入方式仅支持 api / self_hosted"}, status_code=422)
    gpu_count = 0
    if deploy_type == "self_hosted":
        try:
            gpu_count = int(body.get("gpu_count") or 0)
        except (TypeError, ValueError):
            return JSONResponse({"error": "卡数需为整数"}, status_code=422)
        if not (1 <= gpu_count <= 512):
            return JSONResponse({"error": "自有部署需填写卡数（1-512）"}, status_code=422)
        if not pin and not pout:
            pin = round(0.15 * gpu_count, 2)
            pout = round(0.30 * gpu_count, 2)
    conn.execute(
        "INSERT INTO models (model_id, model_name, display_name, provider, endpoint, credential_ref, price_input, "
        "price_output, capabilities, status, bank_coverage, latency_ms_base, profile, deploy_type, gpu_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)",
        (body["model_id"], model_name, body["display_name"], provider, body["endpoint"], body["credential_ref"],
         pin, pout,
         db.j(body.get("capabilities") or {"tool_call": True, "streaming": True, "context_window": 32768,
                                           "vision": bool(body.get("vision"))}),
         "active", int(body.get("latency_ms_base", 800)),
         db.j(body.get("profile") or {"general": 0.7}), deploy_type, gpu_count))
    conn.execute("UPDATE models SET bank_coverage=1.0 WHERE model_id=?", (body["model_id"],))
    conn.commit()
    db.audit("demo-admin", "model_register", {"model_id": body["model_id"], "model_name": model_name, "provider": provider})
    return {"ok": True, "model_id": body["model_id"], "same_name_count": same_name,
            "note": "模型已上线并纳入智能判维路由；benchmark 得分缺失时在「模型画像」页补录。"}


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
    if body.get("allow_agg_override") is not None:
        params["allow_agg_override"] = 1 if body["allow_agg_override"] else 0
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
    name = (row["name"] or "策略") + "_副本"
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


def _get_setting(key, default=None):
    row = db.get_conn().execute("SELECT v FROM kv_settings WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def _set_setting(key, val):
    conn = db.get_conn()
    conn.execute("INSERT INTO kv_settings (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=?", (key, val, val))
    conn.commit()


# ============ 智能路由模型（硬依赖：未配置时线上请求直连兜底） ============

@app.get("/api/settings/router-model")
def get_router_model_setting():
    return {"router": router_core.get_router_model()}


@app.post("/api/settings/router-model")
async def set_router_model(request: Request):
    """智能路由模型独立接入：每次请求由它判定相关 benchmark 维度。传空 model_id 为移除。"""
    body = await request.json()
    mid = (body.get("model_id") or "").strip()
    if not mid:
        _set_setting("router_model_info", "")
        db.audit("demo-admin", "router_model_set", {"model_id": "(移除)"})
        return {"ok": True, "router": None}
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
    _set_setting("router_model_info", db.j(info))
    db.audit("demo-admin", "router_model_set", {"model_id": mid, "display_name": info["display_name"]})
    return {"ok": True, "router": info}


# ============ 模型画像 = Benchmark 得分表 + 成本（静态配置，即改即生效） ============

@app.get("/api/benchmark")
def get_benchmark():
    """画像原始表：维度定义 + 各模型分数（手动修正覆盖榜单快照，null=缺失、平均时跳过）。"""
    dims, scores, meta = router_core.get_bench_profile()
    conn = db.get_conn()
    models = [{"model_id": r["model_id"], "display_name": r["display_name"],
               "price_input": r["price_input"], "price_output": r["price_output"]}
              for r in conn.execute("SELECT model_id, display_name, price_input, price_output "
                                    "FROM models WHERE status='active'")]
    return {"dims": dims, "scores": scores, "models": models,
            "asof": meta["asof"], "source": meta["source"], "overrides": meta["overrides"],
            "router": router_core.get_router_model()}


@app.post("/api/benchmark/score")
async def set_benchmark_score(request: Request):
    """手动修正单格分数（自有部署 / 微调模型无公开分时补录；传 null 标记缺失）。业务校准的人工入口。"""
    body = await request.json()
    mid = (body.get("model_id") or "").strip()
    dim = (body.get("dim") or "").strip()
    if dim not in {d["key"] for d in mockmodels.BENCH_DIMS}:
        return JSONResponse({"error": "未知维度"}, status_code=422)
    conn = db.get_conn()
    if not conn.execute("SELECT 1 FROM models WHERE model_id=?", (mid,)).fetchone():
        return JSONResponse({"error": "模型不存在"}, status_code=404)
    score = body.get("score", "missing")
    if score is not None:
        try:
            score = float(score)
        except (TypeError, ValueError):
            return JSONResponse({"error": "得分需为 0-100 的数字，或 null 标记缺失"}, status_code=422)
        if not (0 <= score <= 100):
            return JSONResponse({"error": "得分需在 0-100 之间"}, status_code=422)
    overrides = db.dj(_get_setting("benchmark_overrides"), {}) or {}
    overrides.setdefault(mid, {})[dim] = score
    _set_setting("benchmark_overrides", db.j(overrides))
    db.audit("demo-admin", "benchmark_score_set", {"model_id": mid, "dim": dim, "score": score})
    return {"ok": True}


@app.post("/api/benchmark/score/reset")
async def reset_benchmark_score(request: Request):
    """撤销单格手动修正，恢复榜单快照值。"""
    body = await request.json()
    mid, dim = (body.get("model_id") or "").strip(), (body.get("dim") or "").strip()
    overrides = db.dj(_get_setting("benchmark_overrides"), {}) or {}
    if mid in overrides and dim in overrides[mid]:
        overrides[mid].pop(dim)
        if not overrides[mid]:
            overrides.pop(mid)
        _set_setting("benchmark_overrides", db.j(overrides))
        db.audit("demo-admin", "benchmark_score_reset", {"model_id": mid, "dim": dim})
    return {"ok": True}


@app.post("/api/benchmark/refresh")
async def refresh_benchmark():
    """刷新榜单快照（演示：更新快照日期；生产为拉取各榜单最新分数）。手动修正保留不被覆盖。"""
    _set_setting("benchmark_asof", time.strftime("%Y-%m"))
    db.audit("demo-admin", "benchmark_refresh", {"asof": time.strftime("%Y-%m")})
    return {"ok": True, "asof": time.strftime("%Y-%m")}


@app.get("/api/profile")
def routing_profile(alpha: float = Query(0.7, ge=0.0, le=1.0), policy_id: str = None):
    """路由效果（benchmark 画像 + 策略 α 即时换算）：每行一个维度，
    综合分 = α × (维度分/100) + (1-α) × 省钱分（单价归一）；缺失分数的格不参与。"""
    conn = db.get_conn()
    dims, scores, meta = router_core.get_bench_profile()
    models = {r["model_id"]: dict(r) for r in conn.execute(
        "SELECT model_id, display_name, price_input, price_output, is_default FROM models WHERE status='active'")}
    pol = None
    if policy_id:
        prow = conn.execute("SELECT * FROM policies WHERE policy_id=?", (policy_id,)).fetchone()
        if prow:
            pol = dict(prow)
            wl = db.dj(pol.get("model_whitelist"), []) or []
            if wl:
                models = {k: v for k, v in models.items() if k in wl}
            alpha = (db.dj(pol.get("params"), {}) or {}).get("alpha", alpha)
    if not models:
        return {"generated": True, "clusters": [], "alpha": alpha, "models": {}, "asof": meta["asof"]}
    prices = {mid: m["price_input"] + m["price_output"] for mid, m in models.items()}
    inv = {mid: 1.0 / max(0.01, p) for mid, p in prices.items()}
    lo, hi = min(inv.values()), max(inv.values())
    eff = {mid: round((v - lo) / (hi - lo), 3) if hi > lo else 0.5 for mid, v in inv.items()}
    rows = []
    for d in dims:
        cell = {}
        for mid in models:
            raw = scores.get(mid, {}).get(d["key"])
            perf = round(raw / 100.0, 3) if raw is not None else None
            combined = round(alpha * perf + (1 - alpha) * eff[mid], 3) if perf is not None else None
            cell[mid] = {"perf": perf, "eff": eff[mid], "combined": combined, "raw": raw,
                         "override": d["key"] in (meta["overrides"].get(mid) or {})}
        valid = {m: s["combined"] for m, s in cell.items() if s["combined"] is not None}
        best = max(valid, key=valid.get) if valid else None
        agg_with = None
        allow_agg = pol.get("allow_aggregation") if pol else 1
        if allow_agg and len(valid) >= 2:
            r2 = sorted(valid.items(), key=lambda x: -x[1])
            t_val = (db.dj(pol.get("params"), {}) or {}).get("t", 0.8) if pol else 0.8
            if r2[0][1] - r2[1][1] < max(0.02, (1 - float(t_val)) * 0.3):
                agg_with = r2[1][0]
        rows.append({"domain": d["key"], "label": d["label"], "bench": d["bench"],
                     "scores": cell, "best": best, "agg_with": agg_with})
    return {"generated": True, "asof": meta["asof"], "alpha": alpha, "clusters": rows,
            "models": {mid: m["display_name"] for mid, m in models.items()},
            "router": router_core.get_router_model()}


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
    # 兑现删除确认文案：使用该风格的产品回退到默认风格（否则 sia.css token 悬空）
    conn = db.get_conn()
    conn.execute("UPDATE products SET brand_file='brand-tokens.default.json' WHERE brand_file=?", (fn,))
    conn.commit()
    if os.path.basename(fn) != fn or not fn.startswith("brand-tokens.") or not fn.endswith(".json"):
        return JSONResponse({"error": "非法的风格文件名"}, status_code=422)
    path = os.path.join(BASE, "brand", fn)
    if not os.path.exists(path):
        return JSONResponse({"error": "风格文件不存在"}, status_code=404)
    os.remove(path)
    # 引用该风格的产品回退默认，避免悬空引用
    conn = db.get_conn()
    n_reset = conn.execute("UPDATE products SET brand_file='brand-tokens.default.json' WHERE brand_file=?", (fn,)).rowcount
    conn.commit()
    db.audit("demo-admin", "brand_delete", {"file": fn, "products_reset": n_reset})
    return {"ok": True, "products_reset": n_reset}


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
    if len(json.dumps(tokens)) > 100_000:
        return JSONResponse({"error": "风格代码过大（上限 100KB），请只保留 design token 字段"}, status_code=422)
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
