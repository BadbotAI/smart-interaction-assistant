"""SQLite 数据层。JSON 字段以 TEXT 存储，读写经 j()/dj() 编解码。"""
import json
import os
import sqlite3
import threading
import time
import uuid

DB_PATH = os.environ.get("PLATFORM_DB", os.path.join(os.path.dirname(__file__), "platform.db"))

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def dj(text, default=None):
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def new_id() -> str:
    return str(uuid.uuid4())


def now_ts() -> float:
    return time.time()


SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
  card_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  component_type TEXT NOT NULL,
  semantic_category TEXT NOT NULL,
  trigger_description TEXT DEFAULT '',
  trigger_examples TEXT DEFAULT '[]',
  model_invokable INTEGER DEFAULT 1,
  field_bindings TEXT DEFAULT '{}',
  option_source TEXT DEFAULT '{"type":"static","values":[]}',
  validation TEXT DEFAULT '{}',
  group_mode TEXT,
  style_overrides TEXT DEFAULT '{}',
  text_templates TEXT DEFAULT '{}',
  emit_fields TEXT DEFAULT '[]',
  emit_targets TEXT DEFAULT '["dashboard"]',
  label_polarity_map TEXT,
  status TEXT DEFAULT 'draft',
  version INTEGER DEFAULT 0,
  published_at REAL,
  created_at REAL,
  updated_at REAL,
  lock_version INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS card_snapshots (
  card_id TEXT,
  version INTEGER,
  snapshot TEXT,
  published_at REAL,
  archived INTEGER DEFAULT 0,
  PRIMARY KEY (card_id, version)
);
CREATE TABLE IF NOT EXISTS card_refs (
  agent_id TEXT,
  card_id TEXT,
  version INTEGER,
  PRIMARY KEY (agent_id, card_id)
);
CREATE TABLE IF NOT EXISTS models (
  model_id TEXT PRIMARY KEY,
  display_name TEXT,
  provider TEXT,
  endpoint TEXT,
  credential_ref TEXT,
  price_input REAL,
  price_output REAL,
  capabilities TEXT DEFAULT '{}',
  status TEXT DEFAULT 'registering',
  bank_coverage REAL DEFAULT 0,
  latency_ms_base INTEGER DEFAULT 800,
  profile TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS bank_queries (
  query_id TEXT PRIMARY KEY,
  tenant_id TEXT,
  embedding TEXT,
  text_ref TEXT,
  query_text TEXT,
  domain_tags TEXT DEFAULT '[]',
  created_at REAL,
  ttl_days INTEGER DEFAULT 365,
  source TEXT DEFAULT 'public'
);
CREATE TABLE IF NOT EXISTS bank_responses (
  query_id TEXT,
  model_id TEXT,
  response_embedding TEXT,
  completion_tokens INTEGER,
  label_value REAL,
  label_confidence REAL,
  label_source TEXT,
  label_kind TEXT DEFAULT 'capability',
  created_at REAL,
  updated_at REAL,
  PRIMARY KEY (query_id, model_id)
);
CREATE TABLE IF NOT EXISTS policies (
  policy_id TEXT PRIMARY KEY,
  name TEXT,
  scope TEXT DEFAULT 'global',
  tenant_id TEXT,
  scene TEXT,
  params TEXT,
  latency_tier TEXT DEFAULT 'balanced',
  allow_aggregation INTEGER DEFAULT 1,
  explore_ratio REAL DEFAULT 0.05,
  model_whitelist TEXT DEFAULT '[]',
  budget_cap TEXT DEFAULT '{}',
  enabled INTEGER DEFAULT 1,
  ab_group TEXT,
  ab_split INTEGER DEFAULT 50,
  version INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS policy_history (
  policy_id TEXT,
  version INTEGER,
  snapshot TEXT,
  ts REAL,
  PRIMARY KEY (policy_id, version)
);
CREATE TABLE IF NOT EXISTS traces (
  trace_id TEXT PRIMARY KEY,
  tenant_id TEXT,
  session_id TEXT,
  turn_id TEXT,
  user_id TEXT,
  ts REAL,
  status TEXT DEFAULT 'ok',
  switch_result TEXT,
  query_text TEXT,
  final_model TEXT,
  total_cost REAL DEFAULT 0,
  total_latency_ms INTEGER DEFAULT 0,
  is_explore INTEGER DEFAULT 0,
  policy_id TEXT,
  ab_group TEXT
);
CREATE TABLE IF NOT EXISTS spans (
  span_id TEXT PRIMARY KEY,
  trace_id TEXT,
  span_type TEXT,
  ts REAL,
  duration_ms INTEGER DEFAULT 0,
  status TEXT DEFAULT 'ok',
  payload TEXT DEFAULT '{}',
  seq INTEGER
);
CREATE TABLE IF NOT EXISTS route_decisions (
  trace_id TEXT PRIMARY KEY,
  tenant_id TEXT,
  policy_id TEXT,
  policy_version INTEGER,
  decision TEXT
);
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  trace_id TEXT,
  tenant_id TEXT,
  session_id TEXT,
  turn_id TEXT,
  user_id TEXT,
  ts REAL,
  event_type TEXT,
  card TEXT,
  route_context TEXT,
  payload TEXT,
  group_info TEXT,
  label_hint TEXT,
  schema_version TEXT DEFAULT '1.0.0',
  admitted INTEGER DEFAULT 1,
  reject_reason TEXT
);
CREATE TABLE IF NOT EXISTS labels (
  label_id TEXT PRIMARY KEY,
  event_id TEXT,
  trace_id TEXT,
  tenant_id TEXT,
  model_id TEXT,
  label_kind TEXT,
  value REAL,
  confidence REAL,
  source TEXT,
  status TEXT DEFAULT 'admitted',
  reason TEXT,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS kv_settings (
  k TEXT PRIMARY KEY,
  v TEXT
);
CREATE TABLE IF NOT EXISTS quota_usage (
  tenant_id TEXT,
  day TEXT,
  tokens INTEGER DEFAULT 0,
  cost REAL DEFAULT 0,
  requests INTEGER DEFAULT 0,
  PRIMARY KEY (tenant_id, day)
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL,
  actor TEXT,
  action TEXT,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS group_votes (
  render_id TEXT,
  trace_id TEXT,
  participant TEXT,
  choice TEXT,
  ts REAL,
  PRIMARY KEY (render_id, participant)
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_traces_ts ON traces(ts);
CREATE INDEX IF NOT EXISTS idx_bankq_tenant ON bank_queries(tenant_id);
CREATE INDEX IF NOT EXISTS idx_labels_ts ON labels(created_at);
"""


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    # 增量迁移：问卷模版化改造新增字段
    try:
        conn.execute("ALTER TABLE cards ADD COLUMN echo_results INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # 列已存在
    # 增量迁移：测试流量打标（测试抽屉提交不污染回显 / 看板 / 标签）
    try:
        conn.execute("ALTER TABLE events ADD COLUMN channel TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # 增量迁移：默认兜底模型（路由故障切换目标，标书 F-5-04）
    try:
        conn.execute("ALTER TABLE models ADD COLUMN is_default INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # 增量迁移：场景数据集题目可带参考答案（Judge 对照打分）
    try:
        conn.execute("ALTER TABLE bank_queries ADD COLUMN ideal TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def audit(actor: str, action: str, detail: dict):
    conn = get_conn()
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, detail) VALUES (?,?,?,?)",
        (now_ts(), actor, action, j(detail)),
    )
    conn.commit()
