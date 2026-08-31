"""链路追踪：Trace 与 Span 的记录与查询（§4.3）。"""
from . import db


class TraceRecorder:
    def __init__(self, trace_id, tenant_id, session_id, turn_id, user_id, query_text, policy_id, ab_group):
        self.trace_id = trace_id
        self.seq = 0
        conn = db.get_conn()
        conn.execute(
            "INSERT INTO traces (trace_id, tenant_id, session_id, turn_id, user_id, ts, query_text, policy_id, ab_group) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (trace_id, tenant_id, session_id, turn_id, user_id, db.now_ts(), query_text, policy_id, ab_group),
        )
        conn.commit()

    def span(self, span_type: str, payload: dict, duration_ms: int = 0, status: str = "ok"):
        self.seq += 1
        conn = db.get_conn()
        conn.execute(
            "INSERT INTO spans (span_id, trace_id, span_type, ts, duration_ms, status, payload, seq) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (db.new_id(), self.trace_id, span_type, db.now_ts(), duration_ms, status, db.j(payload), self.seq),
        )
        conn.commit()

    def finish(self, switch_result, final_model, total_cost, total_latency_ms, is_explore, status="ok"):
        conn = db.get_conn()
        conn.execute(
            "UPDATE traces SET switch_result=?, final_model=?, total_cost=?, total_latency_ms=?, is_explore=?, status=? "
            "WHERE trace_id=?",
            (switch_result, final_model, total_cost, total_latency_ms, 1 if is_explore else 0, status, self.trace_id),
        )
        conn.commit()


def add_span(trace_id: str, span_type: str, payload: dict, duration_ms: int = 0, status: str = "ok"):
    """给已存在的 Trace 追加 span（用于回答产生后的卡片交互与标签产出）。"""
    conn = db.get_conn()
    row = conn.execute("SELECT MAX(seq) AS m FROM spans WHERE trace_id=?", (trace_id,)).fetchone()
    seq = (row["m"] or 0) + 1
    conn.execute(
        "INSERT INTO spans (span_id, trace_id, span_type, ts, duration_ms, status, payload, seq) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (db.new_id(), trace_id, span_type, db.now_ts(), duration_ms, status, db.j(payload), seq),
    )
    conn.commit()


def get_trace(trace_id: str, unmask: bool = False, actor: str = "demo-admin"):
    conn = db.get_conn()
    t = conn.execute("SELECT * FROM traces WHERE trace_id=?", (trace_id,)).fetchone()
    if not t:
        return None
    spans = conn.execute("SELECT * FROM spans WHERE trace_id=? ORDER BY seq", (trace_id,)).fetchall()
    trace = dict(t)
    if not unmask:
        trace["query_text"] = mask_text(trace.get("query_text"))
    else:
        db.audit(actor, "trace_unmask", {"trace_id": trace_id})
    out_spans = []
    for s in spans:
        sp = dict(s)
        sp["payload"] = db.dj(sp["payload"], {})
        if not unmask:
            for key in ("query", "content", "final_content"):
                if key in sp["payload"] and isinstance(sp["payload"][key], str):
                    sp["payload"][key] = mask_text(sp["payload"][key])
        out_spans.append(sp)
    trace["spans"] = out_spans
    return trace


def mask_text(text):
    """Trace 原文默认脱敏展示（§4.6）：保留首尾，中间遮蔽。"""
    if not text:
        return text
    if len(text) <= 6:
        return text[0] + "*" * (len(text) - 1)
    return text[:4] + "*" * min(12, len(text) - 6) + text[-2:]


def list_traces(filters: dict, limit: int = 50):
    conn = db.get_conn()
    where, args = ["1=1"], []
    if filters.get("switch_result"):
        where.append("switch_result=?"); args.append(filters["switch_result"])
    if filters.get("status"):
        where.append("status=?"); args.append(filters["status"])
    if filters.get("min_cost"):
        where.append("total_cost>=?"); args.append(float(filters["min_cost"]))
    if filters.get("min_latency"):
        where.append("total_latency_ms>=?"); args.append(int(filters["min_latency"]))
    if filters.get("is_explore"):
        where.append("is_explore=1")
    if filters.get("final_model"):
        where.append("final_model=?"); args.append(filters["final_model"])
    if filters.get("since"):
        where.append("ts>?"); args.append(float(filters["since"]))
    # 调用模式存于 route_decisions.decision JSON（走查 D3：看板明细下钻与图表同源）
    if filters.get("mode"):
        mode = filters["mode"]
        if mode == "auto":
            where.append("(trace_id NOT IN (SELECT trace_id FROM route_decisions WHERE json_extract(decision,'$.mode') IS NOT NULL "
                         "AND json_extract(decision,'$.mode')!='auto'))")
        else:
            where.append("trace_id IN (SELECT trace_id FROM route_decisions WHERE json_extract(decision,'$.mode')=?)")
            args.append(mode)
    rows = conn.execute(
        f"SELECT * FROM traces WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ? OFFSET ?",
        (*args, limit, int(filters.get("offset") or 0))
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["query_text"] = mask_text(d.get("query_text"))
        out.append(d)
    return out
