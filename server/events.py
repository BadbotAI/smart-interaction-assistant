"""事件管道与标签回流（§3.5、§5.2、§5.3）。

P1 交互事件 → 清洗与准入 → 候选标签池 → EmbeddingBank（租户层）。
前端事件绝不直接写 bank。
"""
from . import db, embeddings, traces

DAILY_FEEDBACK_CAP = 200  # 单用户单日反馈数上限，超过标记可疑

REQUIRED_FIELDS = ["event_id", "trace_id", "tenant_id", "session_id", "turn_id", "user_id", "event_type"]

VALID_EVENT_TYPES = {
    "card_rendered", "card_interaction_started", "card_submitted", "card_abandoned",
    "feedback_given", "control_invoked", "render_degraded", "card_ref_missing",
}


def ingest(evt: dict) -> dict:
    """接入单条交互事件。返回 {accepted, reject_reason, labels_created}。"""
    for f in REQUIRED_FIELDS:
        if not evt.get(f):
            return {"accepted": False, "reject_reason": f"missing_{f}"}
    if evt["event_type"] not in VALID_EVENT_TYPES:
        return {"accepted": False, "reject_reason": "unknown_event_type"}

    conn = db.get_conn()
    if conn.execute("SELECT 1 FROM events WHERE event_id=?", (evt["event_id"],)).fetchone():
        return {"accepted": True, "reject_reason": "duplicate_event_id", "labels_created": 0}  # 幂等

    admitted, reject_reason = 1, None
    hashed_user = _hash_user(evt["user_id"])

    # 准入规则 1：同一用户对同一 turn 的同类反馈只计一次
    if evt["event_type"] == "feedback_given":
        dup = conn.execute(
            "SELECT 1 FROM events WHERE user_id=? AND turn_id=? AND event_type='feedback_given' "
            "AND json_extract(card,'$.component_type')=? AND admitted=1",
            (hashed_user, evt["turn_id"], (evt.get("card") or {}).get("component_type"))).fetchone()
        if dup:
            admitted, reject_reason = 0, "duplicate_feedback_for_turn"

    # 准入规则 1.5：同一 render 的组件提交只计一次（防重放刷回显 / 看板）
    if admitted and evt["event_type"] == "card_submitted":
        rid = (evt.get("payload") or {}).get("render_id")
        if rid:
            dup = conn.execute(
                "SELECT 1 FROM events WHERE user_id=? AND event_type='card_submitted' AND admitted=1 "
                "AND json_extract(payload,'$.render_id')=?", (hashed_user, rid)).fetchone()
            if dup:
                admitted, reject_reason = 0, "duplicate_submission_for_render"

    # 准入规则 2：单用户单日反馈超阈值 → 可疑，不自动入库
    if admitted and evt["event_type"] == "feedback_given":
        day_start = db.now_ts() - 86400
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM events WHERE user_id=? AND event_type='feedback_given' AND ts>?",
            (hashed_user, day_start)).fetchone()["c"]
        if n >= DAILY_FEEDBACK_CAP:
            admitted, reject_reason = 0, "daily_feedback_cap_exceeded"

    conn.execute(
        """INSERT INTO events (event_id, trace_id, tenant_id, session_id, turn_id, user_id, ts, event_type,
           card, route_context, payload, group_info, label_hint, schema_version, admitted, reject_reason, channel)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (evt["event_id"], evt["trace_id"], evt["tenant_id"], evt["session_id"], evt["turn_id"],
         hashed_user, db.now_ts(), evt["event_type"],
         db.j(evt.get("card") or {}), db.j(evt.get("route_context") or {}),
         db.j(evt.get("payload") or {}), db.j(evt.get("group") or None),
         db.j(evt.get("label_hint") or None), evt.get("schema_version", "1.0.0"),
         admitted, reject_reason, evt.get("channel") or ""))
    conn.commit()

    labels_created = 0
    if admitted and (evt.get("channel") or "") != "test":
        # 测试流量不产标签（不进画像 / 反馈优化）
        labels_created = _process_labels(evt)
        if evt["event_type"] in ("card_submitted", "feedback_given", "control_invoked",
                                 "card_abandoned", "render_degraded", "card_ref_missing"):
            span_type = {"card_submitted": "card_interact", "card_abandoned": "card_interact",
                         "feedback_given": "label_emit", "control_invoked": "card_interact",
                         "render_degraded": "card_render", "card_ref_missing": "card_render"}[evt["event_type"]]
            traces.add_span(evt["trace_id"], span_type, {
                "event_type": evt["event_type"],
                "component_type": (evt.get("card") or {}).get("component_type"),
                "payload_summary": _summarize_payload(evt),
            }, status="degraded" if evt["event_type"] in ("render_degraded", "card_ref_missing") else "ok")
    return {"accepted": bool(admitted), "reject_reason": reject_reason, "labels_created": labels_created}


def _hash_user(user_id: str) -> str:
    """user_id 存储时假名化（§5.1）。"""
    import hashlib
    if user_id.startswith("u_"):
        return user_id
    return "u_" + hashlib.sha256(user_id.encode()).hexdigest()[:16]


def _summarize_payload(evt: dict) -> dict:
    p = evt.get("payload") or {}
    keep = {}
    for k in ("user_selection", "modified_from_default", "time_to_interact_ms", "time_to_submit_ms",
              "action", "reason", "dimension", "value"):
        if k in p:
            keep[k] = p[k]
    g = evt.get("group")
    if g:
        keep["group_distribution"] = g.get("distribution")
    return keep


def _process_labels(evt: dict) -> int:
    """label_hint / preference payload → 标签池 → 租户 bank。"""
    conn = db.get_conn()
    tenant_id = evt["tenant_id"]
    trace_id = evt["trace_id"]
    created = 0

    trace_row = conn.execute("SELECT * FROM traces WHERE trace_id=?", (trace_id,)).fetchone()
    ab_group = trace_row["ab_group"] if trace_row else None
    is_explore = bool(trace_row["is_explore"]) if trace_row else False

    label_specs = []
    card = evt.get("card") or {}
    payload = evt.get("payload") or {}

    if card.get("component_type") == "feedback.preference" and payload.get("selected_model_id"):
        selected = payload["selected_model_id"]
        unselected = payload.get("unselected_model_ids") or []
        label_specs.append({"model_id": selected, "value": 1.0, "confidence": 0.90,
                            "kind": "capability", "source": "explicit_preference"})
        for m in unselected:
            label_specs.append({"model_id": m, "value": 0.0, "confidence": 0.90,
                                "kind": "capability", "source": "explicit_preference"})
    elif evt.get("label_hint"):
        h = evt["label_hint"]
        value = (float(h.get("polarity", 0)) + 1.0) / 2.0
        for m in h.get("target_models") or []:
            label_specs.append({"model_id": m, "value": value,
                                "confidence": float(h.get("confidence", 0.5)),
                                "kind": h.get("label_kind", "capability"),
                                "source": h.get("source", "implicit_behavior")})

    # 信号源闸门：运营可按来源关闭回流（kv_settings: label_source_off:<source> = 1）
    off = {r["k"].split(":", 1)[1] for r in conn.execute(
        "SELECT k FROM kv_settings WHERE k LIKE 'label_source_off:%' AND v='1'").fetchall()}
    label_specs = [sp for sp in label_specs if sp["source"] not in off]

    for spec in label_specs:
        # 准入规则：A/B 实验组流量隔离，不混入基线 bank
        status, reason = "admitted", None
        if ab_group and ab_group != "A":
            status, reason = "isolated", "ab_experiment_group"
        conn.execute(
            "INSERT INTO labels (label_id, event_id, trace_id, tenant_id, model_id, label_kind, value, "
            "confidence, source, status, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (db.new_id(), evt["event_id"], trace_id, tenant_id, spec["model_id"], spec["kind"],
             spec["value"], spec["confidence"], spec["source"], status, reason, db.now_ts()))
        created += 1
        # 准入规则：preference 类标签不得写入 capability 用途的 bank 计算
        if status == "admitted" and spec["kind"] == "capability":
            _apply_to_bank(tenant_id, trace_row, spec, is_explore)
    conn.commit()
    return created


def _apply_to_bank(tenant_id: str, trace_row, spec: dict, is_explore: bool):
    """把已准入的能力标签写入租户增量 bank（在线四元组：query/response/label/token）。"""
    if not trace_row:
        return
    conn = db.get_conn()
    trace_id = trace_row["trace_id"]
    query_text = trace_row["query_text"] or ""
    query_id = "online-" + trace_id
    if not conn.execute("SELECT 1 FROM bank_queries WHERE query_id=?", (query_id,)).fetchone():
        conn.execute(
            "INSERT INTO bank_queries (query_id, tenant_id, embedding, text_ref, query_text, domain_tags, "
            "created_at, ttl_days, source) VALUES (?,?,?,?,?,?,?,?,?)",
            (query_id, tenant_id, db.j(embeddings.embed(query_text)),
             f"vault://queries/{query_id}", query_text, db.j([]), db.now_ts(), 365, "tenant_online"))

    decision_row = conn.execute("SELECT decision FROM route_decisions WHERE trace_id=?", (trace_id,)).fetchone()
    tokens_out, resp_emb = 0, None
    if decision_row:
        decision = db.dj(decision_row["decision"], {})
        for call in decision.get("model_calls", []):
            if call["model_id"] == spec["model_id"]:
                tokens_out = call.get("tokens_out", 0)
                resp_emb = call.get("resp_emb")
                break

    existing = conn.execute("SELECT * FROM bank_responses WHERE query_id=? AND model_id=?",
                            (query_id, spec["model_id"])).fetchone()
    if existing:
        old_v, old_c = existing["label_value"] or 0.5, existing["label_confidence"] or 0.3
        new_c = spec["confidence"]
        merged_v = (old_v * old_c + spec["value"] * new_c) / (old_c + new_c)
        merged_c = min(0.99, old_c + 0.3 * new_c)
        conn.execute(
            "UPDATE bank_responses SET label_value=?, label_confidence=?, label_source=?, updated_at=? "
            "WHERE query_id=? AND model_id=?",
            (merged_v, merged_c, spec["source"], db.now_ts(), query_id, spec["model_id"]))
    else:
        conn.execute(
            "INSERT INTO bank_responses (query_id, model_id, response_embedding, completion_tokens, "
            "label_value, label_confidence, label_source, label_kind, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (query_id, spec["model_id"], db.j(resp_emb) if resp_emb else None, tokens_out,
             spec["value"], spec["confidence"], spec["source"], "capability", db.now_ts(), db.now_ts()))
    conn.commit()
