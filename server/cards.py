"""P1 卡片服务：对象模型、状态机、发布快照、触发匹配、调试（§2.6–2.8）。"""
from . import db, embeddings

PRESENT_TYPES = {
    "text.emphasis", "metric.card", "list.ordered", "timeline", "steps", "table",
    "chart.line", "chart.area", "chart.bar", "chart.pie", "chart.stacked",
    "chart.scatter", "chart.range", "chart.box", "tree", "treemap",
    "graph.network", "map.geo", "gantt", "calendar", "track.map",
    "matrix.compare", "flow.reasoning", "citation.card",
}
COLLECT_TYPES = {
    "select.single", "select.card", "select.multi", "rank.priority", "slider.range",
    "scale.likert", "form.structured", "input.followup", "picker.datetime", "picker.timerange",
    "picker.location", "upload.file", "upload.image", "matrix.compare+select", "suggest.followup", "commerce.order", "entry.link",
}
CONTROL_TYPES = {"control.confirm", "control.interrupt", "control.retry", "control.branch"}
EVALUATE_TYPES = {"feedback.binary", "feedback.preference", "feedback.span"}


def semantic_category(component_type: str) -> str:
    if component_type in PRESENT_TYPES:
        return "present"
    if component_type in COLLECT_TYPES:
        return "collect"
    if component_type in CONTROL_TYPES:
        return "control"
    if component_type in EVALUATE_TYPES:
        return "evaluate"
    raise ValueError(f"unknown component_type: {component_type}")


# ---------- 校验（§2.8 内容异常：保存前校验并定位到具体字段） ----------

def _norm_opt(o):
    """选项文本归一：trim + 全角空格折叠，用于查重。"""
    return str(o).replace("\u3000", " ").strip()


def validate_card(payload: dict, strict: bool = False) -> list:
    errors = []
    name = (payload.get("name") or "").strip()
    if not name:
        errors.append({"field": "name", "message": "名称必填"})
    elif len(name) > 15:
        errors.append({"field": "name", "message": "配置名称不能超过 15 字"})
    if len(payload.get("description") or "") > 256:
        errors.append({"field": "description", "message": "描述不能超过 256 字符"})
    ct = payload.get("component_type")
    try:
        semantic_category(ct)
    except (ValueError, TypeError):
        errors.append({"field": "component_type", "message": "组件类型不合法"})
        return errors
    trig = payload.get("trigger_description") or ""
    if payload.get("model_invokable", True) and semantic_category(ct) in ("collect", "control") and not trig.strip():
        errors.append({"field": "trigger_description", "message": "允许 AI 触发的配置必须填写触发条件描述"})
    if len(trig) > 200:
        errors.append({"field": "trigger_description", "message": "触发条件不能超过 200 字"})
    examples = payload.get("trigger_examples") or []
    if len(examples) > 10:
        errors.append({"field": "trigger_examples", "message": "示例问法最多 10 条"})
    templates = payload.get("text_templates") or {}
    for key, tpl in templates.items():
        if isinstance(tpl, str) and tpl.count("{") != tpl.count("}"):
            errors.append({"field": f"text_templates.{key}", "message": "文案模板变量括号不匹配"})
    style = payload.get("style_overrides") or {}
    allowed_tokens = {"color.primary", "radius.card", "radius.control", "font.size_base", "spacing.card_padding", "density"}
    for key in style:
        if key not in allowed_tokens:
            errors.append({"field": f"style_overrides.{key}", "message": f"非法样式 token：{key}（仅允许 design token 覆盖，禁止任意 CSS）"})
    lpm = payload.get("label_polarity_map")
    if lpm and lpm.get("label_kind") not in ("capability", "preference"):
        errors.append({"field": "label_polarity_map.label_kind", "message": "label_kind 必须为 capability 或 preference"})
    if strict:
        errors.extend(validate_card_content(payload))
    group = payload.get("group_mode")
    if group:
        if semantic_category(ct) != "collect":
            errors.append({"field": "group_mode", "message": "群体决策模式仅采集型组件可开启"})
        elif group.get("feedback_to_model") not in ("result_only", "distribution", None):
            errors.append({"field": "group_mode.feedback_to_model", "message": "取值必须为 result_only 或 distribution"})
    return errors


def validate_card_content(payload: dict) -> list:
    """内容强校验：发布时执行（与前端 clientValidate 对齐）。草稿允许半成品。"""
    errors = []
    ct = payload.get("component_type") or ""
    config = ((payload.get("field_bindings") or {}).get("config") or {})
    options = [o for o in (config.get("options") or []) if _norm_opt(o)]
    select_types = ("select.single", "select.multi", "select.card", "matrix.compare+select", "suggest.followup")
    if ct in select_types or ct == "rank.priority":
        if len(options) < 2:
            errors.append({"field": "options", "message": "至少 2 个选项"})
    if ct == "commerce.order" and len(options) < 1:
        errors.append({"field": "options", "message": "至少 1 个商品"})
    if ct == "entry.link" and len(options) < 1:
        errors.append({"field": "options", "message": "至少 1 个入口"})
    if options:
        normed = [_norm_opt(o) for o in options]
        if len(set(normed)) != len(normed):
            errors.append({"field": "options", "message": "选项有重复（空格与全角空格视为相同）"})
    if ct == "matrix.compare+select" and len([d for d in (config.get("dimensions") or []) if str(d).strip()]) < 1:
        errors.append({"field": "dimensions", "message": "至少 1 个对比维度"})
    if ct == "slider.range":
        sl = config.get("slider") or {}
        try:
            if not (float(sl.get("min", 0)) < float(sl.get("max", 100))):
                errors.append({"field": "slider_max", "message": "最大值必须大于最小值"})
        except (TypeError, ValueError):
            errors.append({"field": "slider_max", "message": "滑杆区间必须是数字"})
    if ct == "scale.likert":
        lk = config.get("likert") or {}
        try:
            f, t = int(lk.get("from", 1)), int(lk.get("to", 5))
            if not (t > f and 2 <= (t - f + 1) <= 11):
                errors.append({"field": "likert", "message": "刻度档位需在 2-11 档之间"})
        except (TypeError, ValueError):
            errors.append({"field": "likert", "message": "刻度必须是整数"})
    if ct == "form.structured":
        import re as _re
        fields = [f for f in (config.get("fields") or []) if (f.get("key") or f.get("label"))]
        if not fields:
            errors.append({"field": "fields", "message": "至少 1 个采集字段"})
        else:
            keys = [(f.get("key") or "").strip() for f in fields]
            if any(not k for k in keys):
                errors.append({"field": "fields", "message": "每个字段都需要字段标识"})
            elif any(not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) for k in keys):
                errors.append({"field": "fields", "message": "字段标识只能用英文、数字、下划线，且不能以数字开头"})
            elif len(set(keys)) != len(keys):
                errors.append({"field": "fields", "message": "字段标识重复"})
    import re as _re2
    wh = (config.get("webhook") or "").strip()
    if wh and not _re2.fullmatch(r"https://\S+", wh):
        errors.append({"field": "webhook", "message": "Webhook 必须是 https:// 开头的完整 URL"})
    for opt, act in (config.get("option_actions") or {}).items():
        api = ((act or {}).get("api") or "").strip()
        if api and not _re2.fullmatch(r"https://\S+", api):
            errors.append({"field": "options", "message": f"选项「{opt}」的跳转链接必须是 https:// 开头的完整 URL"})
    for opt, meta in (config.get("option_meta") or {}).items():
        img = ((meta or {}).get("image") or "").strip()
        if img and not (img.startswith("http://") or img.startswith("https://") or img.startswith("data:image/")):
            errors.append({"field": "options", "message": f"选项「{opt}」的配图需为 http(s) 链接或本地上传的图片"})
    v = config.get("validity") or {}
    for k in ("from", "until"):
        if v.get(k) and not _re2.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?", str(v[k])):
            errors.append({"field": "valid_until", "message": "有效期格式需为 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM"})
    if v.get("from") and v.get("until") and str(v["from"]) > str(v["until"]):
        errors.append({"field": "valid_until", "message": "失效日期不能早于生效日期"})
    return errors


CARD_FIELDS = [
    "name", "description", "trigger_description", "trigger_examples", "model_invokable",
    "field_bindings", "option_source", "validation", "group_mode", "style_overrides",
    "text_templates", "emit_fields", "emit_targets", "label_polarity_map", "echo_results",
]
JSON_FIELDS = {"trigger_examples", "field_bindings", "option_source", "validation", "group_mode",
               "style_overrides", "text_templates", "emit_fields", "emit_targets", "label_polarity_map"}


def row_to_card(row) -> dict:
    card = dict(row)
    for f in JSON_FIELDS:
        card[f] = db.dj(card.get(f))
    card["model_invokable"] = bool(card["model_invokable"])
    card["echo_results"] = bool(card.get("echo_results"))
    return card


def create_card(tenant_id: str, payload: dict):
    errors = validate_card(payload)
    if errors:
        return None, errors
    conn = db.get_conn()
    dup = conn.execute("SELECT 1 FROM cards WHERE tenant_id=? AND name=? AND status!='deleted'",
                       (tenant_id, payload["name"])).fetchone()
    if dup:
        return None, [{"field": "name", "message": "同名配置已存在（租户内唯一）"}]
    card_id = payload.get("card_id") or db.new_id()  # 幂等提交：客户端可预生成 id 重试
    exists = conn.execute("SELECT card_id FROM cards WHERE card_id=?", (card_id,)).fetchone()
    if exists:
        return get_card(card_id), []
    ct = payload["component_type"]
    now = db.now_ts()
    conn.execute(
        """INSERT INTO cards (card_id, tenant_id, name, description, component_type, semantic_category,
           trigger_description, trigger_examples, model_invokable, field_bindings, option_source,
           validation, group_mode, style_overrides, text_templates, emit_fields, emit_targets,
           label_polarity_map, echo_results, status, version, created_at, updated_at, lock_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'draft',0,?,?,0)""",
        (card_id, tenant_id, payload["name"], payload.get("description", ""), ct, semantic_category(ct),
         payload.get("trigger_description", ""), db.j(payload.get("trigger_examples") or []),
         1 if payload.get("model_invokable", True) else 0,
         db.j(payload.get("field_bindings") or {}),
         db.j(payload.get("option_source") or {"type": "static", "values": []}),
         db.j(payload.get("validation") or {}),
         db.j(payload["group_mode"]) if payload.get("group_mode") else None,
         db.j(payload.get("style_overrides") or {}), db.j(payload.get("text_templates") or {}),
         db.j(payload.get("emit_fields") or []), db.j(payload.get("emit_targets") or ["dashboard"]),
         db.j(payload["label_polarity_map"]) if payload.get("label_polarity_map") else None,
         1 if payload.get("echo_results") else 0,
         now, now))
    conn.commit()
    return get_card(card_id), []


def get_card(card_id: str):
    row = db.get_conn().execute("SELECT * FROM cards WHERE card_id=?", (card_id,)).fetchone()
    return row_to_card(row) if row else None


def update_card(card_id: str, payload: dict, lock_version: int):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM cards WHERE card_id=?", (card_id,)).fetchone()
    if not row:
        return None, [{"field": "_", "message": "配置已被删除。可将当前内容另存为新配置。", "code": "card_deleted"}]
    if row["status"] == "deleted":
        return None, [{"field": "_", "message": "配置已被删除。可将当前内容另存为新配置。", "code": "card_deleted"}]
    if row["lock_version"] != lock_version:
        current = row_to_card(row)
        return None, [{"field": "_", "code": "conflict", "message": "他人已修改此配置，请对比差异后选择合并或覆盖。",
                       "server_state": current}]
    merged = row_to_card(row)
    if "component_type" in payload and payload["component_type"] != row["component_type"]:
        return None, [{"field": "component_type", "message": "组件类型不可变更，换组件请新建配置"}]
    new_name = (payload.get("name") or "").strip()
    if new_name and new_name != row["name"]:
        dup = conn.execute("SELECT 1 FROM cards WHERE tenant_id=? AND name=? AND status!='deleted' AND card_id!=?",
                           (row["tenant_id"], new_name, card_id)).fetchone()
        if dup:
            return None, [{"field": "name", "message": "同名配置已存在（租户内唯一）"}]
    merged.update({k: v for k, v in payload.items() if k in CARD_FIELDS})
    errors = validate_card(merged)
    if errors:
        return None, errors
    # 编辑已发布卡片 → 回到草稿态（§2.7）
    new_status = "draft" if row["status"] in ("published", "draft") else row["status"]
    sets, args = [], []
    for f in CARD_FIELDS:
        if f in payload:
            sets.append(f"{f}=?")
            v = payload[f]
            if f in JSON_FIELDS:
                args.append(db.j(v) if v is not None else None)
            elif f in ("model_invokable", "echo_results"):
                args.append(1 if v else 0)
            else:
                args.append(v)
    sets.extend(["status=?", "updated_at=?", "lock_version=lock_version+1"])
    args.extend([new_status, db.now_ts(), card_id])
    conn.execute(f"UPDATE cards SET {', '.join(sets)} WHERE card_id=?", args)
    conn.commit()
    return get_card(card_id), []


def _merge_option_aliases(conn, card_id: str, prev_card, new_card):
    """按 option_ids 对齐新旧选项：同一 id 文案变化 → 记录 old→new 别名；旧别名链同步指向最新文案。"""
    cfg_new = ((new_card.get("field_bindings") or {}).get("config") or {})
    if prev_card is None:
        return
    cfg_old = ((prev_card.get("field_bindings") or {}).get("config") or {})
    ids_old = cfg_old.get("option_ids") or []
    # 老配置的选项可能只存在 option_source.values（config.options 为空）
    opts_old = cfg_old.get("options") or ((prev_card.get("option_source") or {}).get("values") or [])
    ids_new, opts_new = cfg_new.get("option_ids") or [], cfg_new.get("options") or []
    # 迁移兜底：旧快照没有 option_ids（历史配置）且选项数量一致时，按位置对齐识别改名
    if not ids_old and opts_old and opts_new and len(opts_old) == len(opts_new) and ids_new:
        ids_old = list(ids_new)
    if not ids_old or not ids_new:
        return
    old_by_id = {i: o for i, o in zip(ids_old, opts_old)}
    aliases = {**dict(cfg_old.get("option_aliases") or {}), **dict(cfg_new.get("option_aliases") or {})}
    changed = False
    for i, o_new in zip(ids_new, opts_new):
        o_old = old_by_id.get(i)
        if o_old and o_old != o_new:
            aliases[o_old] = o_new
            changed = True
    if changed or aliases:
        # 链式压缩：a→b、b→c 归并为 a→c；指向自身的清掉
        def resolve(label, seen=None):
            seen = seen or set()
            while label in aliases and label not in seen:
                seen.add(label)
                label = aliases[label]
            return label
        aliases = {k: resolve(v) for k, v in aliases.items() if resolve(v) != k}
        cfg_new["option_aliases"] = aliases
        fb = new_card.get("field_bindings") or {}
        fb["config"] = cfg_new
        conn.execute("UPDATE cards SET field_bindings=? WHERE card_id=?", (db.j(fb), card_id))


def resolve_option_alias(config: dict, label: str) -> str:
    """历史提交的旧文案按别名链归并到当前文案。"""
    aliases = (config or {}).get("option_aliases") or {}
    seen = set()
    while label in aliases and label not in seen:
        seen.add(label)
        label = aliases[label]
    return label


def transition(card_id: str, action: str, actor: str = "demo-admin", force: bool = False):
    """状态机迁移（§2.7）。返回 (card, error)。"""
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM cards WHERE card_id=?", (card_id,)).fetchone()
    if not row:
        return None, {"message": "配置不存在"}
    status = row["status"]

    if action == "publish":
        if status not in ("draft", "offline"):
            return None, {"message": f"当前状态 {status} 不可发布"}
        card = row_to_card(row)
        errors = validate_card(card, strict=True)
        if errors:
            return None, {"message": "发布失败：配置校验未通过，请修正后重试。配置保持草稿态。", "errors": errors}
        prev_snap_row = conn.execute(
            "SELECT snapshot FROM card_snapshots WHERE card_id=? ORDER BY version DESC LIMIT 1", (card_id,)).fetchone()
        prev_card = db.dj(prev_snap_row["snapshot"], {}) if prev_snap_row else None
        # 原样重新上线：内容与最新快照一致的下线配置，恢复上线即可，不升版本、不惊动引用方
        if status == "offline" and prev_card is not None:
            keys = ("name", "trigger_description", "trigger_examples", "component_type",
                    "field_bindings", "option_source", "text_templates")
            if all(db.j(card.get(k)) == db.j(prev_card.get(k)) for k in keys):
                conn.execute("UPDATE cards SET status='published', updated_at=? WHERE card_id=?",
                             (db.now_ts(), card_id))
                conn.commit()
                db.audit(actor, "card_republish_same", {"card_id": card_id, "version": row["version"]})
                return get_card(card_id), None
        # 选项改名别名链：同一选项位（option_ids 对齐）文案变化时，历史数据按别名归并到新文案
        _merge_option_aliases(conn, card_id, prev_card, card)
        card = row_to_card(conn.execute("SELECT * FROM cards WHERE card_id=?", (card_id,)).fetchone())
        new_version = row["version"] + 1
        now = db.now_ts()
        conn.execute("UPDATE card_snapshots SET archived=1 WHERE card_id=?", (card_id,))
        card["version"] = new_version
        card["status"] = "published"
        conn.execute(
            "INSERT INTO card_snapshots (card_id, version, snapshot, published_at, archived) VALUES (?,?,?,?,0)",
            (card_id, new_version, db.j(card), now))
        conn.execute("UPDATE cards SET status='published', version=?, published_at=?, updated_at=? WHERE card_id=?",
                     (new_version, now, now, card_id))
        conn.commit()
        db.audit(actor, "card_publish", {"card_id": card_id, "version": new_version})
        return get_card(card_id), None

    if action == "restore_draft":
        # 把历史快照内容载入为当前草稿（不发布、不动线上）
        snap = conn.execute("SELECT snapshot FROM card_snapshots WHERE card_id=? AND version=?",
                            (card_id, int(force or 0))).fetchone()
        if not snap:
            return None, {"message": "指定版本不存在"}
        old = db.dj(snap["snapshot"], {})
        sets, args = [], []
        for f in CARD_FIELDS:
            if f in old:
                sets.append(f"{f}=?")
                v = old[f]
                if f in JSON_FIELDS:
                    args.append(db.j(v) if v is not None else None)
                elif f in ("model_invokable", "echo_results"):
                    args.append(1 if v else 0)
                else:
                    args.append(v)
        sets.extend(["status='draft'", "updated_at=?", "lock_version=lock_version+1"])
        args.extend([db.now_ts(), card_id])
        conn.execute(f"UPDATE cards SET {', '.join(sets)} WHERE card_id=?", args)
        conn.commit()
        db.audit(actor, "card_restore_draft", {"card_id": card_id, "from_version": int(force or 0)})
        return get_card(card_id), None

    if action == "offline":
        # published，或编辑中的已上线配置（status=draft 但存在生效快照）均可下线
        if not (status == "published" or (status == "draft" and row["version"] >= 1)):
            return None, {"message": "仅已上线配置可下线"}
        conn.execute("UPDATE cards SET status='offline', updated_at=? WHERE card_id=?", (db.now_ts(), card_id))
        conn.commit()
        db.audit(actor, "card_offline", {"card_id": card_id})
        return get_card(card_id), None

    if action == "delete":
        serving = status == "published" or (status == "draft" and row["version"] >= 1)
        if serving and not force:
            return None, {"message": "该配置线上仍在服务，请先下线再删除"}
        refs = conn.execute("SELECT agent_id, version FROM card_refs WHERE card_id=?", (card_id,)).fetchall()
        if refs and not force:
            return None, {"message": "配置被以下 Agent 引用，删除将导致运行时降级为纯文本。确认影响后可选择强制删除。",
                          "code": "referenced", "refs": [dict(r) for r in refs]}
        conn.execute("UPDATE cards SET status='deleted', updated_at=? WHERE card_id=?", (db.now_ts(), card_id))
        # 历史快照保留，不随卡片删除消失（§2.7）
        conn.commit()
        db.audit(actor, "card_delete", {"card_id": card_id, "force": force, "refs": len(refs)})
        return get_card(card_id), None

    if action == "rollback":
        version = force  # 复用参数位：rollback 时 force 传目标版本号
        snap = conn.execute("SELECT snapshot FROM card_snapshots WHERE card_id=? AND version=?",
                            (card_id, version)).fetchone()
        if not snap:
            return None, {"message": "目标版本不存在"}
        old = db.dj(snap["snapshot"], {})
        new_version = row["version"] + 1
        now = db.now_ts()
        sets, args = [], []
        for f in CARD_FIELDS:
            sets.append(f"{f}=?")
            v = old.get(f)
            if f in JSON_FIELDS:
                args.append(db.j(v) if v is not None else None)
            elif f in ("model_invokable", "echo_results"):
                args.append(1 if v else 0)
            else:
                args.append(v)
        sets.extend(["status='published'", "version=?", "published_at=?", "updated_at=?"])
        args.extend([new_version, now, now, card_id])
        conn.execute(f"UPDATE cards SET {', '.join(sets)} WHERE card_id=?", args)
        conn.execute("UPDATE card_snapshots SET archived=1 WHERE card_id=?", (card_id,))
        card = get_card(card_id)
        conn.execute("INSERT INTO card_snapshots (card_id, version, snapshot, published_at, archived) VALUES (?,?,?,?,0)",
                     (card_id, new_version, db.j(card), now))
        conn.commit()
        db.audit(actor, "card_rollback", {"card_id": card_id, "to_version": version, "new_version": new_version})
        return card, None

    return None, {"message": f"未知操作 {action}"}


# ---------- 触发匹配与调试（§2.6 trigger_description 调试闭环） ----------

def published_invokable_cards(tenant_id: str):
    """线上可触发的场景 = 最新发布快照。

    编辑已发布场景会让 cards 行回到 draft，但线上必须继续服务上一个发布版本
    （快照语义，§2.7）——直到管理员再次发布。下线/删除的场景不服务。"""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM cards WHERE tenant_id=? AND model_invokable=1 "
        "AND semantic_category IN ('collect','control') "
        "AND (status='published' OR (status='draft' AND version>=1))", (tenant_id,)).fetchall()
    from datetime import datetime as _dt
    def _in_validity(card_dict):
        """有效期之外的配置不参与触发。值可为 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM（精确到分钟）；
        纯日期归一为 from=当天 00:00 / until=当天 23:59 后再与当前时刻比较。"""
        v = ((card_dict.get("field_bindings") or {}).get("config") or {}).get("validity") or {}
        now = _dt.now().strftime("%Y-%m-%dT%H:%M")
        vf, vu = v.get("from"), v.get("until")
        if vf:
            vf = vf if "T" in str(vf) else str(vf) + "T00:00"
            if now < vf:
                return False
        if vu:
            vu = vu if "T" in str(vu) else str(vu) + "T23:59"
            if now > vu:
                return False
        return True
    out = []
    for r in rows:
        card = row_to_card(r)
        if card["status"] == "draft":
            snap = conn.execute("SELECT snapshot FROM card_snapshots WHERE card_id=? AND archived=0",
                                (card["card_id"],)).fetchone()
            if not snap:
                continue
            snap_card = db.dj(snap["snapshot"], {})
            if not snap_card:
                continue
            snap_card["card_id"] = card["card_id"]
            card = snap_card
        if not _in_validity(card):
            continue
        out.append(card)
    return out


def match_score(query: str, card: dict) -> float:
    """query 与卡片触发描述/示例的相似度（取最大）。"""
    q_emb = embeddings.embed(query)
    texts = [card.get("trigger_description") or ""] + (card.get("trigger_examples") or [])
    best = 0.0
    for t in texts:
        if t.strip():
            best = max(best, embeddings.cosine(q_emb, embeddings.embed(t)))
    return best


HIT_THRESHOLD = 0.35


def match_cards(query: str, tenant_id: str):
    """返回 (命中卡片或 None, 竞争列表)。"""
    cards = published_invokable_cards(tenant_id)
    scored = sorted(((match_score(query, c), c) for c in cards), key=lambda x: -x[0])
    competitors = [{"card_id": c["card_id"], "name": c["name"], "component_type": c["component_type"],
                    "score": round(s, 4), "hit": s >= HIT_THRESHOLD} for s, c in scored[:6]]
    hit = scored[0][1] if scored and scored[0][0] >= HIT_THRESHOLD else None
    return hit, competitors


def resolve_options(card: dict, query: str):
    """按 option_source 解析选项。api 失败时返回 (None, 降级原因)。"""
    src = card.get("option_source") or {}
    stype = src.get("type", "static")
    if stype == "static":
        return src.get("values") or [], None
    if stype == "model_generated":
        base = src.get("hint_values") or ["方案一", "方案二", "方案三"]
        return base, None
    if stype == "api":
        endpoint = src.get("endpoint", "")
        if "suppliers" in endpoint:
            return ["华骏国际货代", "中远供应链", "环球捷运"], None
        return None, f"选项来源 API 无响应（{endpoint or '未配置端点'}）"
    return [], None


# ---------- 信息模版库与 AI 匹配（问卷模版能力） ----------

TEMPLATE_LIBRARY = [
    # 选择类
    {"component_type": "select.single", "name": "文本选择器", "desc": "文本选项中做选择；配置里可切换单选 / 多选，样式可选列表 / 胶囊 / 输入框浮现",
     "keywords": ["选择", "哪个", "选一个", "单选", "多选", "哪些", "倾向", "勾选"],
     "default_config": {"options": ["开专票", "开普票"], "recommended_default": "开普票"}},
    {"component_type": "select.card", "name": "卡片选择器", "desc": "带图片、标题与文案的卡片式选择，适合方案 / 套餐类",
     "keywords": ["方案", "套餐", "版本", "对比图"],
     "default_config": {"options": ["海运直达", "海铁联运"], "option_meta": {"海运直达": {"desc": "35 天 · 成本低，适合不赶时间"}, "海铁联运": {"desc": "26 天 · 快 9 天，成本略高"}}}},
    {"component_type": "rank.priority", "name": "优先级排序器", "desc": "把候选项按重要程度排出先后",
     "keywords": ["排序", "优先级", "先后", "重要", "顺序"],
     "default_config": {"options": ["时效最快", "成本最低", "风险最小"]}},
    {"component_type": "matrix.compare+select", "name": "对比选择器", "desc": "多个候选按几项指标对比后选定一项",
     "keywords": ["对比", "比选", "权衡", "比较", "优劣"],
     "default_config": {"options": ["中远供应链", "环球捷运"], "dimensions": ["价格", "时效", "合规"]}},
    # 评价类
    {"component_type": "scale.likert", "name": "评分器", "desc": "在刻度上打分；刻度可选 1-5 / 0-5 / 0-3 / 1-10 / 0-10 / -2~+2",
     "keywords": ["满意", "同意", "程度", "评分", "打分", "认可", "NPS"],
     "default_config": {"likert": {"left": "非常不满意", "right": "非常满意", "from": 1, "to": 5}}},
    # 填写类
    {"component_type": "form.structured", "name": "表单收集工具", "desc": "表单式一次收集多个结构化字段",
     "keywords": ["填写", "登记", "信息", "表单", "资料"],
     "default_config": {"fields": [{"key": "order_no", "label": "订单号", "type": "text", "required": True}, {"key": "phone", "label": "联系电话", "type": "text", "required": True}]}},
    {"component_type": "slider.range", "name": "数值选择器", "desc": "在区间内取一个数；样式可选滑杆 / 步进器 / 直接填写",
     "keywords": ["多少", "数值", "比例", "预算", "百分比", "金额"],
     "default_config": {"slider": {"min": 0, "max": 100, "unit": ""}}},
    {"component_type": "input.followup", "name": "备注填写器", "desc": "单条自由文本，适合备注类场景（如送货备注）",
     "keywords": ["备注", "留言", "要求", "说明", "补充说明"],
     "default_config": {"placeholder": "例如：周五前送到，放前台即可"}},
    {"component_type": "picker.datetime", "name": "日期选择器", "desc": "选择日期或日期+时间",
     "keywords": ["日期", "哪天", "预约", "什么时候"],
     "default_config": {}},
    {"component_type": "picker.timerange", "name": "时间段选择器", "desc": "选择一段起止时间 / 日期区间",
     "keywords": ["时间段", "几点到几点", "区间", "起止", "档期"],
     "default_config": {}},
    {"component_type": "picker.location", "name": "地址卡片", "desc": "把配置好的仓库 / 门店地址（含详址）推给用户确认，不读取用户本地地址",
     "keywords": ["地址", "地点", "位置", "送到哪", "收货", "在哪"],
     "default_config": {"options": ["上海仓", "宁波港", "青岛港"]}},
    {"component_type": "upload.file", "name": "文件上传器", "desc": "上传 PDF / Word / Excel 等单据文件（演示不真实上传）",
     "keywords": ["上传", "附件", "文件", "单据", "合同", "报关单", "箱单"],
     "default_config": {"placeholder": "请上传报关单或合同文件（PDF / Excel）"}},
    # 引导类
    {"component_type": "entry.link", "name": "入口跳转器",
     "desc": "对话中插入可点入口：不配链接 = 作为追问发给 AI，配链接 = 直达专区 / 服务页（已合并追问引导能力）",
     "keywords": ["入口", "专区", "活动", "跳转", "更多", "商城", "优惠", "追问", "还想问", "推荐问题"],
     "default_config": {"options": ["神券团购专区", "增值服务商城"],
         "option_meta": {"神券团购专区": {"desc": "咖啡茶饮 5 折起，限时领券"},
                          "增值服务商城": {"desc": "加固 · 保价 · 包装耗材"}},
         "option_actions": {"神券团购专区": {"api": "https://www.example-scm.com/coupon"},
                             "增值服务商城": {"api": "https://www.example-scm.com/vas"}}}},
    {"component_type": "upload.image", "name": "图片上传器", "desc": "拍照或从相册上传图片凭证（演示不真实上传）",
     "keywords": ["拍照", "照片", "图片", "回单", "破损", "凭证", "截图"],
     "default_config": {"placeholder": "请拍摄或上传清晰照片"}},
]


def extract_content(question: str) -> dict:
    """从管理员的场景描述里抽取可预填的内容：引号中的候选选项、疑似提问文案。
    抽出的内容用于渲染「带用户内容的预览」——比通用占位说服力高一个量级。"""
    import re
    text = question or ""
    # 中英文引号内的短语视为候选选项
    quoted = re.findall(r'[“「"\'‘]([^”」"\'’]{1,24})[”」"\'’]', text)
    options = []
    for q in quoted:
        # 引号内还有顿号/逗号分隔的，继续拆
        parts = [p.strip() for p in re.split(r"[、,，/]", q) if p.strip()]
        options.extend(parts)
    # 去重保序
    seen, deduped = set(), []
    for o in options:
        if o not in seen:
            seen.add(o)
            deduped.append(o)
    # 疑似提问文案：描述里"确认/选择/询问 XX"的宾语
    prompt = ""
    m = re.search(r"(?:确认|选择|询问|采集|收集|填写)([^，。；,;]{2,20})", text)
    if m:
        obj = m.group(1).strip("的了 ")
        prompt = f"请选择{obj}" if len(deduped) >= 2 else f"请补充{obj}"
    return {"options": deduped if len(deduped) >= 2 else [], "prompt": prompt}


def suggest_templates(question: str, top_n: int = 3):
    """输入场景描述，AI 匹配合适的交互组件：关键词命中 + 语义相似度混合打分。
    同时抽取描述中的内容（选项/提问）预填进组件配置，供前端实时渲染预览。"""
    q_emb = embeddings.embed(question)
    extracted = extract_content(question)
    scored = []
    for t in TEMPLATE_LIBRARY:
        kw_hits = [w for w in t["keywords"] if w in question]
        kw_score = min(1.0, len(kw_hits) * 0.5)
        sem_score = embeddings.cosine(q_emb, embeddings.embed(t["name"] + t["desc"] + "".join(t["keywords"])))
        score = kw_score * 0.7 + max(0.0, sem_score) * 0.3
        # 描述里抽出了明确选项 → 选择类组件加权
        if extracted["options"] and t["component_type"] in ("select.single", "select.multi", "select.card"):
            score += 0.25
        reason = f"命中关键词：{'、'.join(kw_hits)}" if kw_hits else "按语义相似度推荐"
        if extracted["options"] and t["component_type"] in ("select.single", "select.multi", "select.card"):
            reason += f"；从描述中识别到候选选项：{'、'.join(extracted['options'][:4])}"
        # 预填配置：抽取内容优先，模版默认值兜底
        prefill = dict(t["default_config"])
        if extracted["options"] and "options" in prefill:
            prefill["options"] = extracted["options"]
        scored.append({"component_type": t["component_type"], "name": t["name"], "desc": t["desc"],
                       "score": round(score, 3), "reason": reason,
                       "default_config": t["default_config"], "prefill_config": prefill,
                       "prefill_prompt": extracted["prompt"]})
    scored.sort(key=lambda x: -x["score"])
    return scored[:top_n]


def rewrite_trigger(description: str) -> dict:
    """AI 改写触发条件：把管理员的口语化描述改写成规范触发描述 + 生成示例问法。
    演示环境为规则改写；生产环境替换为 LLM 调用（prompt：把描述改写成
    tool description 风格，明确适用/不适用边界，并生成 3 条典型用户问法）。"""
    raw = (description or "").strip().rstrip("。，,.")
    if not raw:
        return {"trigger_description": "", "trigger_examples": []}
    core = raw
    for prefix in ("当用户", "用户", "当", "如果", "客户"):
        if core.startswith(prefix):
            core = core[len(prefix):]
            break
    for suffix in ("时", "的时候", "的情况下"):
        if core.endswith(suffix):
            core = core[: -len(suffix)]
            break
    polished = (f"当用户{core}时触发本配置。适用于该场景下的咨询、求助与处理请求；"
                f"不适用于普通闲聊或与此无关的问题。触发后按本配置的信息与交互组件引导用户。")
    examples = [f"{core}，怎么处理", f"我遇到了{core}的情况", f"关于{core}想咨询一下"]
    return {"trigger_description": polished, "trigger_examples": examples}


def fill_debug_params(card: dict, query: str):
    """调试面板：模拟模型命中后填入的参数。与线上信封同源（优先场景配置）。"""
    config = (card.get("field_bindings") or {}).get("config") or {}
    templates = card.get("text_templates") or {}
    if config.get("options"):
        options, degrade = list(config["options"]), None
    else:
        options, degrade = resolve_options(card, query)
    params = {"prompt": templates.get("prompt") or f"请选择{card.get('name','')}相关选项"}
    if templates.get("reply"):
        params["reply_text"] = templates["reply"]
    if options is not None:
        params["options"] = options
    if degrade:
        params["degraded"] = degrade
    if card.get("echo_results"):
        params["echo_results"] = True
    return params
