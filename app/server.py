import concurrent.futures
import hashlib
import hmac
import json
import os
import math
import secrets
import sqlite3
import threading
import time
import uuid
import sys
from datetime import datetime, timezone
from .time_utils import APP_TIMEZONE, local_day, day_window
from . import platform, security, mcp_registry, mfa, playbooks, incidents, privacy, lab, operations, health, access, audit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from urllib.error import HTTPError, URLError

from .adapters import adapter_for
from .localization import language as ui_language, translate as translate_ui
from . import workspace, files, pipeline, automation
from .vault import encrypt, decrypt
from .browser_mcp import BrowserMCP, status as browser_status, install as install_browser

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DATABASE_PATH", str(ROOT / "data" / "harbor.db"))
MODE = os.getenv("APP_MODE", "live")
DAILY_LIMIT = None  # Retained in the API for old clients; spending is tracking-only.
MAX_QUEUE = int(os.getenv("MAX_QUEUE", "50"))
WORKERS = int(os.getenv("TASK_WORKERS", "2"))
MAX_PROMPT_CHARS = 16000
MAX_REQUEST_BYTES = 262144
STOP = threading.Event()
SESSIONS = {}
SESSION_LOCK = threading.Lock()
CONFIG_LOCK = threading.RLock()
LOGIN_ATTEMPTS = {}
AGENTS = [
    dict(id="hermes", name="Hermes", role="Operasyon uzmanı", provider="hermes-bridge",
         model="MiniMax M3", hardware="M4 Mac mini · 7/24", color="mint", price=0.002,
         endpoint=os.getenv("HERMES_BRIDGE_URL", ""), key_env="HERMES_BRIDGE_TOKEN",
         instruction="You are an operations expert. Return a concise evidence-based action plan."),
    dict(id="local", name="Atlas", role="Teknik analist", provider="openai-compatible",
         model=os.getenv("LOCAL_MODEL", "local-model"), hardware="Ryzen 5600X · RTX 3060 Ti · 64 GB", color="blue", price=0,
         endpoint=os.getenv("LOCAL_BASE_URL", ""), key_env="",
         instruction="You are a technical analyst. Identify constraints and a testable implementation."),
    dict(id="research", name="Scout", role="Araştırmacı", provider="openai-compatible",
         model=os.getenv("CLOUD_MODEL", "configured-model"), hardware="Bulut sağlayıcı", color="purple", price=0.003,
         endpoint=os.getenv("CLOUD_BASE_URL", ""), key_env="CLOUD_API_KEY",
         instruction="You are a research expert. State uncertainty and do not invent sources."),
    dict(id="critic", name="Sage", role="Baş uzman · eleştirmen", provider="openai-compatible",
         model=os.getenv("JUDGE_MODEL", "configured-judge"), hardware="Seçilebilir sağlayıcı", color="amber", price=0.004,
         endpoint=os.getenv("JUDGE_BASE_URL", ""), key_env="JUDGE_API_KEY",
         instruction="You are a critical reviewer. Synthesize evidence, disagreements and remaining uncertainty."),
]
BY_ID = {a["id"]: a for a in AGENTS}
DEFAULT_AGENTS = [dict(a) for a in AGENTS]


def now():
    return datetime.now(timezone.utc).isoformat()


def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def log(db, task_id, event, message, audit_target=None):
    db.execute("INSERT INTO logs(task_id,event,message,created_at) VALUES(?,?,?,?)",
               (task_id, event, message, now()))
    audit.emit(db,event,task_id,audit_target)


def initialize():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,role TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS credentials(user_id TEXT PRIMARY KEY REFERENCES users(id),salt TEXT NOT NULL,password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agent_configs(id TEXT PRIMARY KEY,config TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deleted_costs(id TEXT PRIMARY KEY,day TEXT NOT NULL,amount REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(
          id TEXT PRIMARY KEY, title TEXT NOT NULL,prompt TEXT NOT NULL,mode TEXT NOT NULL,
          agents TEXT NOT NULL,judge TEXT,status TEXT NOT NULL,owner_id TEXT NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL,updated_at TEXT NOT NULL,budget REAL NOT NULL,reserved REAL NOT NULL,
          cost REAL NOT NULL DEFAULT 0,tool_requested INTEGER NOT NULL DEFAULT 0,error TEXT,synthesis TEXT);
        CREATE TABLE IF NOT EXISTS runs(
          id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),agent_id TEXT NOT NULL,
          kind TEXT NOT NULL,status TEXT NOT NULL,output TEXT,input_tokens INTEGER DEFAULT 0,
          output_tokens INTEGER DEFAULT 0,cost REAL DEFAULT 0,error TEXT,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals(
          id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),tool TEXT NOT NULL,
          arguments TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',decided_by TEXT REFERENCES users(id),
          created_at TEXT NOT NULL,decided_at TEXT);
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT REFERENCES tasks(id),
          event TEXT NOT NULL,message TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status,created_at);
        CREATE INDEX IF NOT EXISTS runs_task ON runs(task_id);
        CREATE INDEX IF NOT EXISTS logs_task ON logs(task_id,id);
        """)
        if "day_timezone" not in {row[1] for row in db.execute("PRAGMA table_info(deleted_costs)")}:
            db.execute("ALTER TABLE deleted_costs ADD COLUMN day_timezone TEXT NOT NULL DEFAULT 'UTC'")
        columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
        for column in ("finish_reason TEXT", "output_limit INTEGER"):
            if column.split()[0] not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN " + column)
        if "provider_state" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
            db.execute("ALTER TABLE runs ADD COLUMN provider_state TEXT NOT NULL DEFAULT ''")
        if "continuation_context" not in {row[1] for row in db.execute("PRAGMA table_info(tasks)")}:
            db.execute("ALTER TABLE tasks ADD COLUMN continuation_context TEXT NOT NULL DEFAULT ''")
        if "web_enabled" not in {row[1] for row in db.execute("PRAGMA table_info(tasks)")}:
            db.execute("ALTER TABLE tasks ADD COLUMN web_enabled INTEGER NOT NULL DEFAULT 0")
        if "resume_state" not in {row[1] for row in db.execute("PRAGMA table_info(tasks)")}:
            db.execute("ALTER TABLE tasks ADD COLUMN resume_state TEXT NOT NULL DEFAULT ''")
        for column in ("request_state TEXT NOT NULL DEFAULT ''", "attempt INTEGER NOT NULL DEFAULT 1", "cost_estimated INTEGER NOT NULL DEFAULT 0"):
            if column.split()[0] not in {r[1] for r in db.execute("PRAGMA table_info(runs)")}:
                db.execute("ALTER TABLE runs ADD COLUMN " + column)
        for column in ("fallback_agent TEXT", "retry_state TEXT NOT NULL DEFAULT ''", "cost_estimated INTEGER NOT NULL DEFAULT 0"):
            if column.split()[0] not in {r[1] for r in db.execute("PRAGMA table_info(tasks)")}:
                db.execute("ALTER TABLE tasks ADD COLUMN " + column)
        workspace.initialize(db)
        access.initialize(db)
        platform.initialize(db)
        mcp_registry.initialize(db)
        mfa.initialize(db)
        playbooks.initialize(db)
        incidents.initialize(db)
        privacy.initialize(db)
        lab.initialize(db)
        operations.initialize(db)
        audit.initialize(db)
        db.execute("CREATE TRIGGER IF NOT EXISTS run_finished AFTER UPDATE OF status ON runs WHEN NEW.status IN ('completed','failed','incomplete','cancelled') AND NEW.finished_at IS NULL BEGIN UPDATE runs SET finished_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE id=NEW.id; END")
        # Legacy calls used a fixed 1,500-token cap and did not record finish_reason.
        db.execute("UPDATE runs SET status='incomplete',finish_reason='legacy_length_suspected',output_limit=1500 WHERE status='completed' AND finish_reason IS NULL AND output_tokens>=1500")
        db.execute("UPDATE tasks SET status='incomplete' WHERE status='completed' AND EXISTS(SELECT 1 FROM runs WHERE runs.task_id=tasks.id AND runs.status='incomplete')")
        db.execute("UPDATE runs SET status='incomplete',finish_reason='empty_response',error='Sağlayıcı boş rapor metni döndürdü; görev tamamlanmadı.' WHERE status='completed' AND trim(coalesce(output,''))=''")
        db.execute("UPDATE tasks SET status='incomplete',error='Sağlayıcı rapor metni döndürmedi. Raporu yeniden üretebilirsin.',updated_at=? WHERE status='completed' AND EXISTS(SELECT 1 FROM runs WHERE runs.task_id=tasks.id AND runs.finish_reason='empty_response')", (now(),))
        for uid, name, role in [("admin", "Emre", "admin"), ("operator", "Operatör", "operator"), ("viewer", "Gözlemci", "viewer")]:
            db.execute("INSERT OR IGNORE INTO users VALUES(?,?,?)", (uid, name, role))
        for a in DEFAULT_AGENTS:
            db.execute("INSERT OR IGNORE INTO agent_configs VALUES(?,?,?)", (a["id"], json.dumps(a), now()))
        # Interrupted calls cannot safely be replayed: preserve evidence and require a new task.
        db.execute("UPDATE runs SET status='failed',error='Sunucu yeniden başladı' WHERE status='running'")
        for row in db.execute("SELECT id FROM tasks WHERE status='running'").fetchall():
            db.execute("UPDATE tasks SET status='failed',error='Sunucu yeniden başladı; otomatik tekrar yapılmadı',reserved=0,updated_at=? WHERE id=?", (now(), row[0]))
            log(db, row[0], "recovery", "Kesilen görev başarısız olarak işaretlendi")
    reload_agents()
    if any(a.get("encrypted_key") for a in AGENTS) and not (Path(DB_PATH).parent / "master.key").is_file():
        raise RuntimeError("Kayıtlı API anahtarlarını açmak için data/master.key yedeğini geri yükle")
    os.chmod(DB_PATH, 0o600)


def reload_agents():
    with CONFIG_LOCK, connect() as db:
        configs = [json.loads(row[0]) for row in db.execute("SELECT config FROM agent_configs ORDER BY rowid")]
        AGENTS[:] = configs
        BY_ID.clear()
        BY_ID.update({a["id"]: a for a in configs})


def requires_setup():
    if MODE == "mock" or os.getenv("AUTH_ADMIN_TOKEN"):
        return False
    with connect() as db:
        return db.execute("SELECT 1 FROM credentials WHERE user_id='admin'").fetchone() is None


def password_record(password):
    if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
        raise ValueError("Parola 12–256 karakter olmalı")
    salt = secrets.token_hex(16)
    if not security.KDF_SLOTS.acquire(blocking=False):raise ValueError("Parola işleyici meşgul; tekrar dene")
    try:digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()
    finally:security.KDF_SLOTS.release()
    return salt, digest


def set_password(uid, password, first_setup=False):
    if first_setup:
        with security.LOCK:
            if not requires_setup():
                raise ValueError("İlk kurulum zaten tamamlanmış")
            _store_password(uid, password, first_setup=True)
            (Path(DB_PATH).parent / 'bootstrap.token').unlink(missing_ok=True)
        return
    _store_password(uid, password)


def _store_password(uid, password, first_setup=False):
    salt, digest = password_record(password)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if first_setup and (os.getenv("AUTH_ADMIN_TOKEN") or db.execute("SELECT 1 FROM credentials WHERE user_id='admin'").fetchone()):
            raise ValueError("İlk kurulum zaten tamamlanmış")
        db.execute("INSERT OR REPLACE INTO credentials VALUES(?,?,?)", (uid, salt, digest))
        log(db, None, "access_updated", f"{uid} erişim parolası güncellendi")
    with SESSION_LOCK:
        for key in [k for k, v in SESSIONS.items() if v["uid"] == uid]:
            SESSIONS.pop(key, None)


def check_login(uid, password):
    if not isinstance(password, str) or len(password) > 256:
        return False
    env_token = os.getenv("AUTH_" + uid.upper() + "_TOKEN", "")
    if env_token and hmac.compare_digest(password, env_token):
        return True
    with connect() as db:
        row = db.execute("SELECT salt,password_hash FROM credentials WHERE user_id=?", (uid,)).fetchone()
    if not row:
        return False
    if not security.KDF_SLOTS.acquire(blocking=False):return False
    try:value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(row[0]), 600000).hex()
    finally:security.KDF_SLOTS.release()
    return hmac.compare_digest(value, row[1])


def agent_key(agent):
    if agent.get("encrypted_key"):
        return decrypt(DB_PATH, agent["encrypted_key"])
    return os.getenv(agent.get("key_env", ""), "") if agent.get("key_env") else ""


def settings_agent(a):
    data = public_agent(a)
    data.update({"endpoint": a["endpoint"], "instruction": a["instruction"],
                 "enabled": a.get("enabled", True), "safe_mode": a.get("safe_mode", False),
                 "has_api_key": bool(a.get("encrypted_key") or (a.get("key_env") and os.getenv(a["key_env"])))})
    return data


def save_agent(user, body):
    if user["role"] != "admin":
        raise PermissionError("Ajan ayarlarını yalnızca yönetici değiştirebilir")
    with CONFIG_LOCK, connect() as db:
        db.execute("BEGIN IMMEDIATE")
        aid = body.get("id") or uuid.uuid4().hex
        if not isinstance(aid, str) or not aid.isalnum() or len(aid) > 64:
            raise ValueError("Geçersiz ajan kimliği")
        old = BY_ID.get(aid, {})
        if old.get("deleted"):
            raise ValueError("Bu ajan silinmiş; yeni bir ajan ekle")
        for task in db.execute("SELECT agents,judge,fallback_agent FROM tasks WHERE status IN ('queued','running')"):
            if aid in json.loads(task[0]) or aid == task[1] or aid == task[2]:
                raise ValueError("Ajanın açık görevi var; görev bitince ayarları değiştirebilirsin")
        agent = dict(old, id=aid)
        for field, default, limit in [("name", "", 80), ("role", "Uzman", 120), ("model", "", 200),
                ("hardware", "Özel sağlayıcı", 200), ("instruction", "You are a helpful expert.", 4000)]:
            value = body.get(field, old.get(field, default))
            if not isinstance(value, str) or len(value) > limit or (field in ("name", "model") and not value.strip()):
                raise ValueError("Ajan adı, model ve diğer metin alanlarını kontrol et")
            agent[field] = value.strip()
        agent["provider"] = body.get("provider", old.get("provider", "openai-compatible"))
        if agent["provider"] not in ("openai-compatible", "hermes-bridge"):
            raise ValueError("Desteklenmeyen adaptör")
        endpoint = body.get("endpoint", old.get("endpoint", ""))
        if not isinstance(endpoint, str) or len(endpoint) > 2000:
            raise ValueError("API adresi geçersiz")
        parsed = urlparse(endpoint.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("API adresi http(s) olmalı; anahtarı URL'ye ekleme")
        if parsed.hostname in ("169.254.169.254", "metadata.google.internal"):
            raise ValueError("Bulut metadata adresi sağlayıcı olarak kullanılamaz")
        agent["endpoint"] = endpoint.strip().rstrip("/")
        agent["enabled"] = bool(body.get("enabled", old.get("enabled", True)))
        agent["safe_mode"] = bool(body.get("safe_mode", old.get("safe_mode", False)))
        agent["color"] = body.get("color", old.get("color", "mint"))
        if agent["color"] not in ("mint", "blue", "purple", "amber"):
            raise ValueError("Geçersiz renk")
        agent["price"] = float(body.get("price", old.get("price", 0)))
        if not math.isfinite(agent["price"]) or not 0 <= agent["price"] <= 100:
            raise ValueError("Birim fiyat 0–100 USD / 1000 token arasında olmalı")
        agent["capabilities"] = platform.capabilities(body, old)
        agent.pop("max_output_tokens", None)
        key = body.get("api_key", "")
        if not isinstance(key, str) or len(key) > 8000:
            raise ValueError("API anahtarı geçersiz")
        if key.strip():
            agent["encrypted_key"] = encrypt(DB_PATH, key.strip())
            agent["key_env"] = ""
        elif body.get("clear_key"):
            agent["encrypted_key"] = ""
            agent["key_env"] = ""
        agent.pop("last_test", None)
        db.execute("INSERT OR REPLACE INTO agent_configs VALUES(?,?,?)", (aid, json.dumps(agent), now()))
        log(db, None, "agent_saved", f"Ajan ayarları kaydedildi · {aid}")
    reload_agents()
    return aid


def change_agent_state(user, aid, enabled=None, delete=False):
    if user["role"] != "admin":
        raise PermissionError("Ajan yönetimi için yönetici rolü gerekli")
    with CONFIG_LOCK, connect() as db:
        db.execute("BEGIN IMMEDIATE")
        old = BY_ID.get(aid)
        if not old or old.get("deleted"):
            raise ValueError("Ajan bulunamadı")
        for task in db.execute("SELECT agents,judge,fallback_agent FROM tasks WHERE status IN ('queued','running')"):
            if aid in json.loads(task[0]) or aid == task[1] or aid == task[2]:
                raise ValueError("Ajanın açık görevi var; bitmesini bekle veya önce iptal et")
        agent = dict(old)
        if delete:
            agent.update(deleted=True, enabled=False, endpoint="", encrypted_key="", key_env="", instruction="")
            agent.pop("last_test", None)
        else:
            if type(enabled) is not bool:
                raise ValueError("Etkinlik durumu true veya false olmalı")
            agent["enabled"] = enabled
        db.execute("UPDATE agent_configs SET config=?,updated_at=? WHERE id=?", (json.dumps(agent), now(), aid))
        log(db, None, "agent_deleted" if delete else "agent_toggled", f"Ajan erişimi güncellendi · {aid}")
    reload_agents()


def delete_history(user, body):
    if user["role"] not in ("admin", "operator"):
        raise PermissionError("Bu rol görev geçmişini silemez")
    ids = body.get("ids", [])
    clear_all = body.get("all") is True
    if not clear_all and (not isinstance(ids, list) or not ids or len(ids) > 1000 or any(not isinstance(x, str) for x in ids)):
        raise ValueError("Silinecek görevleri seç")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if clear_all:
            rows = db.execute("SELECT * FROM tasks WHERE status IN ('completed','failed','cancelled','incomplete') AND reserved=0 AND NOT EXISTS(SELECT 1 FROM runs WHERE runs.task_id=tasks.id AND runs.status='running')" +
                              (" AND owner_id=?" if user["role"] != "admin" else ""), (user["id"],) if user["role"] != "admin" else ()).fetchall()
        else:
            unique = list(dict.fromkeys(ids))
            rows = db.execute("SELECT * FROM tasks WHERE id IN (" + ",".join("?" for _ in unique) + ")", unique).fetchall()
            if len(rows) != len(unique):
                raise ValueError("Görevlerden biri bulunamadı")
            for row in rows:
                if user["role"] != "admin" and row["owner_id"] != user["id"]:
                    raise PermissionError("Yalnızca kendi görevlerini silebilirsin")
                running = db.execute("SELECT 1 FROM runs WHERE task_id=? AND status='running'", (row["id"],)).fetchone()
                if row["status"] not in ("completed", "failed", "cancelled", "incomplete") or row["reserved"] or running:
                    raise ValueError("Çalışan, kuyruktaki veya onay bekleyen görev silinemez")
        if clear_all:rows=[r for r in rows if access.level(db,user,r['project_id'])=='write']
        else:
            for row in rows:access.check(db,user,row['project_id'],True)
        for row in rows:
            playbooks.capture(sys.modules[__name__],db,row["id"])
            lab.capture(sys.modules[__name__],db,row["id"])
            # Keep only anonymous accounting so clearing history cannot reset spending limits.
            db.execute("INSERT INTO deleted_costs(id,day,amount,day_timezone) VALUES(?,?,?,?)", (uuid.uuid4().hex, local_day(row["created_at"]), row["cost"], APP_TIMEZONE))
            (Path(DB_PATH).parent / "artifacts" / (row["id"] + ".txt")).unlink(missing_ok=True)
            for table in ("logs", "approvals", "runs"):
                db.execute("DELETE FROM " + table + " WHERE task_id=?", (row["id"],))
            db.execute("DELETE FROM tasks WHERE id=?", (row["id"],))
        log(db, None, "history_deleted", f"{len(rows)} görev silindi · {user['id']}")
    return len(rows)


def public_agent(a):
    return {k: a[k] for k in ("id", "name", "role", "provider", "model", "hardware", "color", "price")} | {
        "capabilities": a.get("capabilities", {}), "configured": bool(a["endpoint"]), "enabled": a.get("enabled", True),
        "input_overhead": len(a["instruction"].encode()) + 512,
        "status": "mock" if MODE == "mock" else ("configured" if a["endpoint"] and a.get("enabled", True) else "unconfigured")}


def provider_summary(db):
    groups = {}
    for agent in AGENTS:
        if agent.get("deleted") or not agent["endpoint"]:
            continue
        key = (agent["provider"], agent["endpoint"].rstrip("/"))
        status = "not_working" if not agent.get("enabled", True) else "unknown"
        if agent.get("enabled", True):
            observations = []
            test = agent.get("last_test")
            if test:
                observations.append((test["checked_at"], "working" if test["ok"] else "not_working"))
            run = db.execute("SELECT status,created_at FROM runs WHERE agent_id=? AND status IN ('completed','incomplete','failed') ORDER BY created_at DESC LIMIT 1", (agent["id"],)).fetchone()
            if run:
                observations.append((run[1], "not_working" if run[0] == "failed" else "working"))
            if observations:
                status = max(observations)[1]
        groups.setdefault(key, []).append(status)
    result = {"working": 0, "not_working": 0, "unknown": 0}
    for statuses in groups.values():
        status = "working" if "working" in statuses else "unknown" if "unknown" in statuses else "not_working"
        result[status] += 1
    result["total"] = len(groups)
    return result


def task_dict(row):
    task = dict(row)
    task["agents"] = json.loads(task["agents"])
    task.pop("continuation_context", None)
    task.pop("resume_state", None)
    task.pop("retry_state", None)
    task.pop("privacy_seed", None)
    task["mask_sensitive"]=bool(task.get("mask_sensitive"))
    task["allow_unmasked_images"]=bool(task.get("allow_unmasked_images"))
    return task


def public_run(row):
    run = dict(row)
    state = run.pop("provider_state", "")
    run.pop("request_state", None)
    run["can_resume_from_evidence"] = False
    if state:
        try:
            run["can_resume_from_evidence"] = any(message.get("role") == "tool" for message in json.loads(state).get("messages", []))
        except (ValueError, TypeError):
            pass
    run["agent_name"] = run.get("agent_name_snapshot") or BY_ID.get(run["agent_id"], {}).get("name", run["agent_id"])
    return run


def call_reserve(agent, prompt, participants=0):
    # Planning estimate only; this never limits a provider's response length.
    tokens = max(21500, len(prompt.encode()) + len(agent["instruction"].encode()) + 512 + participants * 9600)
    return round(agent["price"] * tokens / 1000, 6)


def reserve_for(ids, judge, prompt=""):
    return round(sum(call_reserve(BY_ID[x], prompt) for x in ids) +
                 (call_reserve(BY_ID[judge], prompt, len(ids)) if judge else 0), 6)


def usage_today(db,user=None):
    if user and user['role']!='admin':
        _,start,end=day_window(now());clause,params=access.sql(user,'tasks.project_id')
        params.update(day_start=start,day_end=end)
        row=db.execute("SELECT coalesce(sum(cost),0),coalesce(sum(CASE WHEN julianday(created_at)>=julianday(:day_start) AND julianday(created_at)<julianday(:day_end) THEN cost ELSE 0 END),0) FROM tasks WHERE "+clause,params).fetchone()
        return {'cost':row[1],'reserved':0,'total_cost':row[0],'timezone':APP_TIMEZONE,'legacy_daily_estimate':False,'scope':'accessible_tasks','includes_deleted':False}
    day, start, end = day_window(now())
    cost = db.execute("SELECT coalesce(sum(cost),0) FROM tasks WHERE julianday(created_at)>=julianday(?) AND julianday(created_at)<julianday(?)", (start, end)).fetchone()[0]
    # Legacy deleted records only retained a UTC date; preserve their day and total.
    deleted = db.execute("SELECT coalesce(sum(amount),0) FROM deleted_costs WHERE day=?", (day,)).fetchone()[0]
    legacy = db.execute("SELECT count(*) FROM deleted_costs WHERE day=? AND day_timezone='UTC'", (day,)).fetchone()[0]
    total = db.execute("SELECT coalesce((SELECT sum(cost) FROM tasks),0)+coalesce((SELECT sum(amount) FROM deleted_costs),0)").fetchone()[0]
    return {"cost": cost + deleted, "reserved": 0, "total_cost": total, "timezone": APP_TIMEZONE, "legacy_daily_estimate": bool(legacy)}


def create_task(user, body, continuation_context="", resume_state="", retry_state="", db=None):
    with CONFIG_LOCK:
        return _create_task(user, body, continuation_context, resume_state, retry_state, db)


def _create_task(user, body, continuation_context="", resume_state="", retry_state="", db=None):
    if user["role"] not in ("admin", "operator"):
        raise PermissionError("Bu rol görev oluşturamaz")
    prompt = body.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError("Bağlam 1–16.000 karakter olmalı")
    mode = body.get("mode", "single")
    ids = body.get("agents", [])
    if mode not in ("single", "council") or not isinstance(ids, list) or not ids or len(ids) > 4 or any(not isinstance(x, str) or x not in BY_ID for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("Geçerli ajanlar ve çalışma modu seçilmeli")
    if mode == "single" and len(ids) != 1:
        raise ValueError("Tek ajan modunda bir ajan seçilmeli")
    judge = body.get("judge") if mode == "council" else None
    if judge is not None and (not isinstance(judge, str) or judge not in BY_ID):
        raise ValueError("Baş uzman geçersiz")
    if MODE != "mock" and any(not BY_ID[x]["endpoint"] for x in ids + ([judge] if judge else [])):
        raise ValueError("Seçilen sağlayıcı sunucuda yapılandırılmamış")
    if any(not BY_ID[x].get("enabled", True) for x in ids + ([judge] if judge else [])):
        raise ValueError("Devre dışı bir ajan seçilemez")
    fallback = body.get("fallback_agent") or None
    if fallback and (fallback not in BY_ID or not BY_ID[fallback].get("enabled", True) or (MODE != "mock" and not BY_ID[fallback]["endpoint"])):
        raise ValueError("Yedek ajan etkin ve yapılandırılmış olmalı")
    web_enabled = body.get("web_enabled") is True
    if web_enabled:
        if not browser_status(DB_PATH)["installed"]:
            raise ValueError("İnterneti açmadan önce ayarlardan Playwright MCP kurulumunu tamamla")
        if any(BY_ID[x]["provider"] != "openai-compatible" for x in ids + ([judge] if judge else []) + ([fallback] if fallback else [])):
            raise ValueError("İnternet modu OpenAI uyumlu araç çağrısı destekleyen ajanlar içindir; Hermes bridge'e bu görevde internet verilmedi")
    lang = body.get('language', 'tr')
    if lang not in ('tr', 'en'):
        raise ValueError('Dil tr veya en olmalı')
    budget = reserve = 0.0
    tid = uuid.uuid4().hex
    from contextlib import nullcontext
    existing = db is not None
    with (nullcontext(db) if existing else connect()) as db:
        if not existing:
            db.execute("BEGIN IMMEDIATE")
        if operations.dispatch_paused(db):raise operations.DispatchPaused('Görev işleme bakım için duraklatıldı; daha sonra yeniden dene')
        options = workspace.task_options(db, user, body, sys.modules[__name__])
        if body.get("mask_sensitive") is True and body.get("allow_unmasked_images") is not True and any(db.execute("SELECT kind FROM files WHERE id=?",(fid,)).fetchone()[0]=="image" for fid in options[1]):raise ValueError("Görsel metni maskelenemez; görselleri çıkar veya maskelenmeden gönderilmesini açıkça onayla")
        privacy_seed=privacy.verify(sys.modules[__name__],db,user,body)
        count = db.execute("SELECT count(*) FROM tasks WHERE status IN ('queued','running','awaiting_approval')").fetchone()[0]
        if count >= MAX_QUEUE:
            raise ValueError("Görev kuyruğu dolu")
        db.execute("INSERT INTO tasks(id,title,prompt,mode,agents,judge,status,owner_id,created_at,updated_at,budget,reserved,tool_requested,continuation_context,web_enabled,resume_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (tid, prompt.strip()[:70], prompt.strip(), mode, json.dumps(ids), judge, "queued", user["id"], now(), now(), budget, reserve, int(bool(body.get("tool_requested", False))), continuation_context, int(web_enabled), resume_state))
        db.execute("UPDATE tasks SET fallback_agent=?,retry_state=? WHERE id=?", (fallback, retry_state, tid))
        workspace.attach(db, tid, options)
        db.execute("UPDATE tasks SET mask_sensitive=?,allow_unmasked_images=?,privacy_seed=? WHERE id=?",(int(body.get("mask_sensitive") is True),int(body.get("allow_unmasked_images") is True),privacy_seed,tid))
        chosen_mcp=mcp_registry.selection(sys.modules[__name__],db,user,body)
        if chosen_mcp and any(BY_ID[x]["provider"]!="openai-compatible" or BY_ID[x].get("capabilities",{}).get("tools") is False for x in ids+([fallback] if fallback else [])):
            raise ValueError("MCP görevleri araç çağrısı destekleyen OpenAI uyumlu ajan gerektirir")
        db.execute("UPDATE tasks SET mcp_selection=? WHERE id=?",(json.dumps(chosen_mcp),tid))
        knowledge = platform.knowledge_context(db, options[0], body.get("knowledge_ids", []))
        if knowledge:
            db.execute("UPDATE tasks SET continuation_context=? WHERE id=?", ((continuation_context+"\n\n"+knowledge).strip(), tid))
        db.execute('UPDATE tasks SET language=? WHERE id=?', (lang, tid))
        log(db, tid, "queued", f"Görev kuyruğa alındı · {user['name']}")
    return tid


def test_agent(user, aid):
    if user["role"] != "admin":
        raise PermissionError("Bağlantı testini yalnızca yönetici başlatabilir")
    with CONFIG_LOCK:
        if aid not in BY_ID:
            raise ValueError("Ajan bulunamadı")
        agent = dict(BY_ID[aid])
        if not agent["endpoint"]:
            raise ValueError("Önce API adresini kaydet")
        agent.update(api_key=agent_key(agent), force_live=True, timeout=15, _probe_limit=32)
        # Test calls have their own reserved task so usage cannot bypass the budget.
        tid = _create_task(user, {"prompt": "Bağlantı testi: Yalnızca OK yaz.", "mode": "single", "agents": [aid], "budget": DAILY_LIMIT})
        with connect() as db:
            db.execute("UPDATE tasks SET status='running' WHERE id=?", (tid,))
    started = time.monotonic()
    message = "API, anahtar ve model doğrulandı"
    success = False
    result = None
    try:
        result = adapter_for(agent).complete(agent, "Yalnızca OK yaz.")
        success = True
    except HTTPError as exc:
        message = {401: "API anahtarı kabul edilmedi (401)", 403: "Sağlayıcı erişimi reddetti (403)",
                   404: "API yolu veya model bulunamadı (404)", 429: "Sağlayıcı kota/hız sınırına ulaşıldı (429)"}.get(exc.code, f"Sağlayıcı HTTP {exc.code} döndürdü")
    except (URLError, TimeoutError, OSError):
        message = "API adresine ulaşılamadı veya 15 saniyelik süre doldu"
    except Exception:
        message = "Yanıt sözleşmesi, model veya Hermes güvenli bridge ayarını kontrol et"
    cost = round((result.input_tokens + result.output_tokens) / 1000 * agent["price"], 6) if result else agent["price"] * 21.5
    info = {"ok": success, "message": message, "latency_ms": round((time.monotonic() - started) * 1000), "checked_at": now(), "task_id": tid}
    with CONFIG_LOCK, connect() as db:
        db.execute("UPDATE tasks SET status=?,cost=?,reserved=0,error=?,synthesis=?,updated_at=? WHERE id=?",
                   ("completed" if success else "failed", cost, None if success else message, "Bağlantı doğrulandı" if success else None, now(), tid))
        row = db.execute("SELECT config FROM agent_configs WHERE id=?", (aid,)).fetchone()
        config = json.loads(row[0])
        config["last_test"] = info
        db.execute("UPDATE agent_configs SET config=?,updated_at=? WHERE id=?", (json.dumps(config), now(), aid))
        log(db, tid, "connection_test", message)
    reload_agents()
    return info


def run_agent(tid, aid, prompt, kind="participant", context="", continuation=False, resume_state=""):
    rid = uuid.uuid4().hex
    with connect() as db:
        task = db.execute("SELECT status,agents,web_enabled FROM tasks WHERE id=?", (tid,)).fetchone()
        if task and task[0]=="running" and not access.owner_can_run(sys.modules[__name__],db,tid):
            db.execute("UPDATE tasks SET status='cancelled',error=?,updated_at=? WHERE id=?",(access.DENIED,now(),tid));return None
        if not task or task[0] != "running":
            return None
        db.execute("INSERT INTO runs(id,task_id,agent_id,kind,status,created_at) VALUES(?,?,?,?,?,?)", (rid, tid, aid, kind, "running", now()))
        db.execute("UPDATE runs SET request_state=? WHERE id=?", (json.dumps({"prompt": prompt, "context": context, "continuation": continuation}, ensure_ascii=False), rid))
        db.execute("UPDATE runs SET model_snapshot=?,provider_snapshot=?,agent_name_snapshot=? WHERE id=?", (BY_ID[aid]["model"], BY_ID[aid]["provider"], BY_ID[aid]["name"], rid))
        log(db, tid, "agent_started", f"{BY_ID[aid]['name']} başladı · {kind}")
    reservation = call_reserve(BY_ID[aid], prompt, len(json.loads(task[1])) if kind == "judge" else 0)
    web = None
    provider_started = False
    circuit=None
    try:
        agent = dict(BY_ID[aid])
        circuit=operations.admit(sys.modules[__name__],agent)
        with connect() as context_db:
            private=context_db.execute("SELECT mask_sensitive,privacy_seed FROM tasks WHERE id=?",(tid,)).fetchone()
            if private and private[0]:agent["_privacy_seed"]=private[1]
            project = context_db.execute("SELECT p.instruction FROM projects p JOIN tasks t ON t.project_id=p.id WHERE t.id=?", (tid,)).fetchone()
            if project:
                agent['instruction'] += '\nProje talimatı:\n' + project[0]
            if kind == 'participant':
                agent['_images'] = [files.image_part(sys.modules[__name__], row) for row in context_db.execute("SELECT f.* FROM files f JOIN task_files t ON t.file_id=f.id WHERE t.task_id=? AND f.kind='image'", (tid,))]
        if agent.get("_privacy_seed"):
            prompt=privacy.redact(prompt,agent["_privacy_seed"]);context=privacy.redact(context,agent["_privacy_seed"]);agent["instruction"]=privacy.redact(agent["instruction"],agent["_privacy_seed"])
        agent["_continuation"] = continuation
        if resume_state:
            saved = json.loads(resume_state)
            agent["_resume_messages"] = saved.get("messages", [])
            agent["_force_final"] = True
            with connect() as checkpoint_db:
                log(checkpoint_db, tid, "report_resume", "Kayıtlı kaynaklarla rapor yeniden üretiliyor; web araması tekrarlanmayacak")
        def save_progress(progress):
            with connect() as checkpoint_db:
                checkpoint_db.execute("UPDATE runs SET provider_state=?,input_tokens=?,output_tokens=? WHERE id=?", (json.dumps(progress, ensure_ascii=False), progress.get("input_tokens", 0), progress.get("output_tokens", 0), rid))
                if progress.get("stage") == "empty_report_retry":
                    log(checkpoint_db, tid, "empty_report_retry", "Boş rapor yanıtı alındı; mevcut kaynaklarla final raporu isteniyor")
        agent["_on_progress"] = save_progress
        if MODE != "mock":
            agent["api_key"] = agent_key(agent)
        if task["web_enabled"] and kind == "participant":
            def web_log(event, message):
                with connect() as web_db:
                    log(web_db, tid, event, message)
            web = BrowserMCP(DB_PATH, on_log=web_log)
            agent["_web"] = web
            def cancelled():
                with connect() as task_db:
                    row = task_db.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
                    return not row or row[0] == "cancelled" or not access.owner_can_run(sys.modules[__name__],task_db,tid)
            agent["_cancelled"] = cancelled
        def cancelled():
            with connect() as task_db:
                row = task_db.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
                return not row or row[0] == "cancelled" or not access.owner_can_run(sys.modules[__name__],task_db,tid)
        agent["_cancelled"] = cancelled
        partial, last_flush = [], [0.0]
        def save_delta(delta):
            partial.append(delta)
            if time.monotonic() - last_flush[0] >= 0.3:
                with connect() as delta_db:
                    delta_db.execute("UPDATE runs SET output=? WHERE id=?", ("".join(partial), rid))
                last_flush[0] = time.monotonic()
        agent["_on_delta"] = save_delta
        with connect() as tool_db:
            task_row=dict(tool_db.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
        if json.loads(task_row.get('mcp_selection','[]')) and kind=='participant':
            web=mcp_registry.Router(sys.modules[__name__],task_row,web)
            agent['_web']=web
        provider_started = True
        for attempt in range(1, 3):
            if cancelled():
                raise RuntimeError("Görev iptal edildi")
            try:
                result = adapter_for(agent).complete(agent, prompt, context)
                break
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                with connect() as check_db:
                    saved = check_db.execute("SELECT provider_state FROM runs WHERE id=?", (rid,)).fetchone()[0]
                # No replay after visible output, a tool round or a completed response.
                transient = not isinstance(exc, HTTPError) or exc.code in (408, 429, 500, 502, 503, 504)
                if attempt == 2 or partial or saved or not transient or cancelled():
                    raise
                with connect() as retry_db:
                    retry_db.execute("UPDATE runs SET attempt=2 WHERE id=?", (rid,))
                    log(retry_db, tid, "provider_retry", "Geçici bağlantı hatası; çıktı alınmadan bir kez yeniden deneniyor")
                if STOP.wait(1):
                    raise RuntimeError("Sunucu durduruluyor")
        operations.success(sys.modules[__name__],circuit)
        finish_reason = result.finish_reason
        if not result.text.strip() and finish_reason in ("stop", "end_turn", "eos", "stop_sequence", "unknown", "completed"):
            finish_reason = "empty_response"
        run_status = "completed" if finish_reason in ("stop", "end_turn", "eos", "stop_sequence", "unknown", "completed") and result.text.strip() else "incomplete"
        cost = 0 if MODE == "mock" else round((result.input_tokens + result.output_tokens) / 1000 * BY_ID[aid]["price"], 6)
        with connect() as db:
            error = "Sağlayıcı kullanıcıya gösterilecek rapor metnini döndürmedi. Raporu yeniden üretebilirsin." if finish_reason == "empty_response" else None
            db.execute("UPDATE runs SET status=?,output=?,input_tokens=?,output_tokens=?,cost=?,finish_reason=?,error=? WHERE id=?", (run_status, result.text, result.input_tokens, result.output_tokens, cost, finish_reason, error, rid))
            estimated = int(bool(getattr(result, 'usage_estimated', False)))
            db.execute('UPDATE runs SET cost_estimated=? WHERE id=?', (estimated, rid))
            db.execute('UPDATE tasks SET cost_estimated=max(cost_estimated,?) WHERE id=?', (estimated, tid))
            # Replace reservation with recorded cost while preserving outstanding headroom.
            db.execute("UPDATE tasks SET cost=cost+?,reserved=max(0,reserved-?),updated_at=? WHERE id=?", (cost, reservation, now(), tid))
            log(db, tid, "agent_" + run_status, f"{BY_ID[aid]['name']} · " + ("Yanıt tamamlanmadı" if run_status == "incomplete" else "Tamamlandı"))
        return result.text
    except Exception as exc:
        if provider_started and operations.transient(exc):operations.failure(sys.modules[__name__],circuit)
        elif provider_started:operations.success(sys.modules[__name__],circuit)
        if "partial" in locals() and partial:
            with connect() as partial_db:
                partial_db.execute("UPDATE runs SET output=? WHERE id=?", ("".join(partial), rid))
        # Provider exception bodies may contain prompts, endpoints or credentials: never expose them.
        with connect() as db:
            possible_cost = 0 if MODE == "mock" or not provider_started else reservation
            if possible_cost:
                db.execute('UPDATE runs SET cost_estimated=1 WHERE id=?', (rid,))
                db.execute('UPDATE tasks SET cost_estimated=1 WHERE id=?', (tid,))
            db.execute("UPDATE runs SET status='failed',cost=?,error=? WHERE id=?", (possible_cost, "Sağlayıcı geçici dinlenmede; Operasyon ekranını kontrol et" if isinstance(exc,operations.CircuitOpen) else "Sağlayıcı yanıtı alınamadı; sunucu yapılandırmasını kontrol edin", rid))
            db.execute("UPDATE tasks SET cost=cost+?,reserved=max(0,reserved-?),updated_at=? WHERE id=?", (possible_cost, reservation, now(), tid))
            log(db, tid, "agent_failed", f"{BY_ID[aid]['name']} sağlayıcı çağrısı başarısız")
        return None
    finally:
        operations.release(sys.modules[__name__],circuit)
        if web:
            web.close()


def process_task(tid):
    with connect() as db:
        row = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        if not row:
            return
        task = task_dict(row)
        continuation_context = row["continuation_context"]
        resume_state = row["resume_state"]
        retry_state = json.loads(row["retry_state"] or "{}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {aid: pool.submit(pipeline.participant, sys.modules[__name__], task, aid, continuation_context, resume_state, retry_state) for aid in task["agents"]}
        results = {aid: future.result() for aid, future in futures.items()}
    with connect() as db:
        row = db.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
        if not row:
            return
        if row[0] == "cancelled":
            db.execute("UPDATE tasks SET reserved=0 WHERE id=?", (tid,))
            return
    failed = any(v is None for v in results.values())
    with connect() as db:
        incomplete = bool(db.execute("SELECT 1 FROM runs WHERE task_id=? AND status='incomplete'", (tid,)).fetchone())
    synthesis = None
    reviews = {}
    if task.get("review_enabled") and len(task["agents"]) > 1 and not failed and not incomplete:
        reviews = pipeline.review(sys.modules[__name__], task, results)
        failed = reviews is None
        with connect() as db:
            incomplete = bool(db.execute("SELECT 1 FROM runs WHERE task_id=? AND status='incomplete'", (tid,)).fetchone())
    if task["judge"] and not failed and not incomplete:
        context = "\n\n".join(BY_ID[k]["name"] + ":\n" + v for k, v in results.items())
        if reviews:
            context += "\n\nKARŞILIKLI ELEŞTİRİLER:\n" + "\n\n".join(BY_ID[k]["name"] + ":\n" + v for k, v in reviews.items())
        context = pipeline.reduce_context(sys.modules[__name__], task, task["judge"], context)
        synthesis = None if context is None else pipeline.execute(sys.modules[__name__], task, task["judge"], task["prompt"] + "\nUzlaşılan noktalar, tartışmalı bulgular, kaynaklar, eksik veri ve önerilen adımlar başlıklarıyla sentezle. Katılımcıların iddialarını otomatik olarak doğrulanmış sayma.", "judge", context)
        failed = synthesis is None
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
        if not row:
            return
        if row[0] == "cancelled":
            db.execute("UPDATE tasks SET reserved=0 WHERE id=?", (tid,))
            return
        incomplete = bool(db.execute("SELECT 1 FROM runs WHERE task_id=? AND status='incomplete'", (tid,)).fetchone())
        status = "failed" if failed else "incomplete" if incomplete else "completed"
        if task["tool_requested"] and not failed and not incomplete:
            status = "awaiting_approval"
            db.execute("INSERT INTO approvals(id,task_id,tool,arguments,created_at) VALUES(?,?,?,?,?)", (
                uuid.uuid4().hex, tid, "artifact.write_text", json.dumps({"filename": tid + ".txt", "source": "task_output"}), now()))
            log(db, tid, "approval_required", "Görev çıktısını yerel dosyaya kaydetmek için yönetici onayı bekleniyor")
        db.execute("UPDATE tasks SET status=?,synthesis=?,reserved=0,error=?,updated_at=? WHERE id=?", (status, synthesis, "En az bir ajan başarısız" if failed else "Sağlayıcı yanıtı tam bitirmedi. Ajan çıktısındaki bitiş bilgisini kontrol et veya devamını üret." if incomplete else None, now(), tid))
        log(db, tid, status, "Ajan akışı sona erdi")


def claim_task():
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if operations.dispatch_paused(db):return None
        row=db.execute("SELECT id FROM tasks WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if not row:return None
        tid=row[0]
        db.execute("UPDATE tasks SET status='running',updated_at=? WHERE id=?",(now(),tid))
        log(db,tid,'running','Kuyruktan alındı')
        return tid


def worker():
    while not STOP.is_set():
        tid = None
        try:
            tid=claim_task()
            if tid:
                process_task(tid)
        except Exception:
            if tid:
                with connect() as db:
                    db.execute("UPDATE tasks SET status='failed',reserved=0,error='Görev işlenemedi',updated_at=? WHERE id=? AND status='running'", (now(), tid))
                    if db.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone():
                        log(db, tid, "failed", "İşleyici hatası")
        STOP.wait(0.3)


def decide(user, aid, decision):
    if user["role"] != "admin":
        raise PermissionError("Araç onayı için yönetici rolü gerekli")
    if decision not in ("approve", "reject"):
        raise ValueError("Geçersiz karar")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
        if not row or row["status"] != "pending":
            raise ValueError("Bekleyen onay bulunamadı")
        task = db.execute("SELECT * FROM tasks WHERE id=?", (row["task_id"],)).fetchone()
        if task["status"] != "awaiting_approval":
            raise ValueError("Görev artık onay beklemiyor")
        status = "rejected"
        if decision == "approve":
            if row["tool"] not in ("artifact.write_text", "demo.write_artifact"):
                raise ValueError("Araç izin listesinde değil")
            # Arguments are produced by the server. Filename is never taken from model/user text.
            folder = Path(DB_PATH).parent / "artifacts"
            folder.mkdir(exist_ok=True)
            output = task["synthesis"] or "\n\n".join(r[0] or "" for r in db.execute("SELECT output FROM runs WHERE task_id=? ORDER BY created_at", (task["id"],)))
            (folder / (task["id"] + ".txt")).write_text(output, encoding="utf-8")
            status = "approved"
        db.execute("UPDATE approvals SET status=?,decided_by=?,decided_at=? WHERE id=?", (status, user["id"], now(), aid))
        db.execute("UPDATE tasks SET status='completed',updated_at=? WHERE id=?", (now(), task["id"]))
        log(db, task["id"], "tool_" + status, f"{row['tool']} · {user['name']}")


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentHarbor/0.1"

    def setup(self):
        super().setup()
        self.request_id=secrets.token_hex(16)
        audit.CONTEXT.set(None)
        self.connection.settimeout(float(os.getenv('HTTP_READ_TIMEOUT', '15')))
        self._header_timer = threading.Timer(float(os.getenv('HTTP_HEADER_DEADLINE','15')), self._abort_headers)
        self._header_timer.daemon=True; self._header_timer.start()

    def _abort_headers(self):
        self._headers_expired=True
        try:self.connection.shutdown(0)
        except OSError:pass

    def finish(self):
        audit.CONTEXT.set(None)
        if hasattr(self,'_header_timer'):self._header_timer.cancel()
        try:super().finish()
        except (OSError,TimeoutError):pass

    def log_message(self, fmt, *args):
        # Do not record request paths or bodies containing user input.
        pass

    def respond(self, code, data, cookie=None):
        if isinstance(data, dict) and 'error' in data:
            data = dict(data, error=translate_ui(data['error'], ui_language(self.headers.get('Accept-Language')), error=True))
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.headers_common("application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def headers_common(self, content_type):
        self.send_header("X-Request-ID",self.request_id)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if security.public_mode():self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def user(self):
        cookie = self.headers.get("Cookie", "")
        sid = next((p.strip()[7:] for p in cookie.split(";") if p.strip().startswith("harbor=")), "")
        with SESSION_LOCK:
            session = SESSIONS.get(sid)
            if not session or session["expires"] < time.time():
                SESSIONS.pop(sid, None)
                raise PermissionError("Oturum açmanız gerekiyor")
            if self.command == "POST" and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
                raise PermissionError("Geçersiz CSRF token")
        with connect() as db:
            return dict(db.execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()), session

    def do_GET(self):
        authenticated=False
        try:
            self._header_timer.cancel()
            if getattr(self,'_headers_expired',False):raise ValueError('HTTP başlık süresi doldu')
            path = urlparse(self.path).path
            if not (path in ('/api/health','/api/health/live','/api/health/ready') and security.loopback(self.client_address[0])):security.allowed_host(self)
            if path == '/api/health/live':
                return self.respond(200, {'ok': True})
            if path == '/api/health/ready':
                available=health.ready(sys.modules[__name__])
                return self.respond(200 if available else 503, {'ok': available})
            if path == "/api/health":
                return self.respond(200, {"ok": True, "mode": MODE})
            security.rate_limit(('get',self.client_address[0]),limit=600)
            if path == "/api/auth/status":
                return self.respond(200, {"mode": MODE, "setup_required": requires_setup()})
            if path.startswith("/api/"):
                user, session = self.user()
                authenticated=True
                with connect() as db:
                    access.preflight(db,user,path,self.path)
                    if audit.handle_get(self,user,path,db,sys.modules[__name__]):return
                    if access.handle_get(self,user,path,db,sys.modules[__name__]):return
                    if path == '/api/security/posture':
                        if user['role']!='admin':raise PermissionError('Yönetici erişimi gerekli')
                        return self.respond(200,security.posture(sys.modules[__name__]) | {'mfa':mfa.statuses(db)})
                    if mcp_registry.handle_get(self,user,path,db,sys.modules[__name__]):
                        return
                    if operations.handle_get(self,user,path,db,sys.modules[__name__]):
                        return
                    if lab.handle_get(self,user,path,db,sys.modules[__name__]):
                        return
                    if incidents.handle_get(self,user,path,db,sys.modules[__name__]):
                        return
                    if playbooks.handle_get(self,user,path,db,sys.modules[__name__]):
                        return
                    if platform.handle_get(self, user, path, db, sys.modules[__name__]):
                        return
                    if workspace.handle_get(self, user, path, db, sys.modules[__name__]):
                        return
                    if path == "/api/browser/status":
                        return self.respond(200, browser_status(DB_PATH))
                    if path == "/api/agents":
                        if user["role"] != "admin":
                            raise PermissionError("Ajan ayarları için yönetici rolü gerekli")
                        return self.respond(200, {"agents": [settings_agent(a) | {"last_test": a.get("last_test")} for a in AGENTS if not a.get("deleted")]})
                    if path == "/api/state":
                        return self.respond(200, {"user": user, "csrf": session["csrf"], "mode": MODE,
                            "providers": provider_summary(db),
                            "browser": browser_status(DB_PATH),
                            "agents": [public_agent(a) for a in AGENTS if not a.get("deleted")], "daily_limit": DAILY_LIMIT,
                            "tasks": [task_dict(r) for r in workspace.task_list(db, self.path,user)],
                            "approvals": [dict(r) for r in db.execute("SELECT a.*,t.title FROM approvals a JOIN tasks t ON a.task_id=t.id WHERE "+access.sql(user,"t.project_id")[0]+" ORDER BY a.created_at DESC LIMIT 100",access.sql(user,"t.project_id")[1])],
                            "usage": usage_today(db,user)})
                    if path.startswith("/api/tasks/") and path.endswith("/export"):
                        from .exports import export_report
                        tid = path.split("/")[-2]
                        row = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
                        if not row:
                            return self.respond(404, {"error": "Görev bulunamadı"})
                        query = parse_qs(urlparse(self.path).query)
                        fmt = query.get("format", ["md"])[0]
                        run_id = query.get("run", [None])[0]
                        runs = [dict(r) for r in db.execute("SELECT * FROM runs WHERE task_id=? ORDER BY created_at", (tid,))]
                        if run_id:
                            runs = [r for r in runs if r["id"] == run_id]
                            if not runs:
                                return self.respond(404, {"error": "Ajan çıktısı bulunamadı"})
                        report_task = task_dict(row)
                        report_task['language'] = query.get('lang', [ui_language(self.headers.get('Accept-Language'))])[0]
                        report_task['sources'] = files.sources(db, tid)
                        content, content_type = export_report(report_task, runs, fmt, BY_ID, single=bool(run_id))
                        self.send_response(200)
                        self.headers_common(content_type)
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Content-Disposition", f'attachment; filename="harbor-{tid[:8]}{("-" + runs[0]["agent_id"]) if run_id else ""}.{fmt}"')
                        self.send_header("Content-Length", str(len(content)))
                        self.end_headers()
                        self.wfile.write(content)
                        return
                    if path.startswith("/api/tasks/"):
                        tid = path.split("/")[-1]
                        row = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
                        if not row:
                            return self.respond(404, {"error": "Görev bulunamadı"})
                        return self.respond(200, {"task": task_dict(row),
                            "sources": files.sources(db, tid),
                            "files": [files.metadata(r) for r in db.execute("SELECT f.* FROM files f JOIN task_files t ON t.file_id=f.id WHERE t.task_id=?", (tid,))],
                            "runs": [public_run(r) for r in db.execute("SELECT * FROM runs WHERE task_id=? ORDER BY created_at", (tid,))],
                            "logs": [dict(r) for r in db.execute("SELECT * FROM logs WHERE task_id=? ORDER BY id", (tid,))]})
                return self.respond(404, {"error": "Endpoint bulunamadı"})
            static = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/settings.js": ("settings.js", "text/javascript; charset=utf-8"), "/history.js": ("history.js", "text/javascript; charset=utf-8"), "/theme.js": ("theme.js", "text/javascript; charset=utf-8"), "/workspace.js": ("workspace.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path == '/brand.svg':
                static[path] = ('brand.svg', 'image/svg+xml')
            if path == '/locales/en.json':
                static[path] = ('locales/en.json', 'application/json; charset=utf-8')
            if path in ('/vendor/marked.umd.js', '/vendor/purify.min.js', '/markdown.js', '/i18n.js', '/time.js', '/platform.js', '/mcp.js', '/playbooks.js', '/incidents.js', '/privacy.js', '/lab.js', '/operations.js', '/project-access.js', '/audit.js', '/recovery.js'):
                static[path] = (path.lstrip('/'), 'text/javascript; charset=utf-8')
            if path not in static:
                return self.respond(404, {"error": "Bulunamadı"})
            filename, ctype = static[path]
            body = (ROOT / "static" / filename).read_bytes()
            self.send_response(200)
            self.headers_common(ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except PermissionError as exc:
            self.respond(403 if authenticated else 401, {"error": str(exc)})
        except ValueError as exc:
            self.respond(400, {"error": str(exc)})
        except Exception:
            self.respond(500, {"error": "Sunucu hatası"})

    def do_POST(self):
        try:
            self._header_timer.cancel()
            if getattr(self,'_headers_expired',False):raise ValueError('HTTP başlık süresi doldu')
            security.allowed_host(self)
            path = urlparse(self.path).path
            # Origin check also protects login/logout against cross-site requests.
            origin = self.headers.get("Origin")
            expected = os.getenv("PUBLIC_ORIGIN", "http://" + self.headers.get("Host", "localhost")).rstrip("/")
            if origin and origin != expected:
                raise PermissionError("Bu origin kabul edilmiyor")
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("JSON içerik gerekli")
            self.close_connection=True
            security.rate_limit(('http',self.client_address[0]), limit=int(os.getenv('HTTP_RATE_PER_MINUTE','240')))
            if path in ('/api/setup','/api/login'):
                security.rate_limit((path,self.client_address[0]),limit=10,window=300)
                if path=='/api/setup' and (security.public_mode() or not requires_setup()):
                    raise PermissionError('İlk kurulum kapalı; yerel kurulumunu tamamla')
                maximum=8192
            elif path.startswith('/api/node-heartbeats/'):
                operations.authenticate(sys.modules[__name__],path.rsplit('/',1)[-1],self.headers.get('Authorization',''))
                maximum=8192
            elif path.startswith('/api/hooks/'):
                token=self.headers.get('Authorization','')
                if not token.startswith('Bearer ') or len(token)>512:
                    raise PermissionError('Webhook yetkisi geçersiz')
                maximum=MAX_REQUEST_BYTES
            else:
                self.user()  # Reject anonymous/CSRF-invalid uploads before reading their body.
                maximum=files.MAX_FILE_BYTES*4//3+8192 if path=='/api/files' else MAX_REQUEST_BYTES
            body=security.read_json_body(self,maximum)
            if not isinstance(body, dict):
                raise ValueError("JSON nesnesi gerekli")
            if path.startswith("/api/node-heartbeats/"):
                return self.respond(200,operations.heartbeat(sys.modules[__name__],path.rsplit("/",1)[-1],self.headers.get("Authorization",""),body))
            if path.startswith("/api/hooks/"):
                token = self.headers.get("Authorization", "")
                if not token.startswith("Bearer "):
                    raise PermissionError("Webhook yetkisi geçersiz")
                audit.begin(self,{'id':'webhook'})
                result = platform.receive_webhook(sys.modules[__name__], path.rsplit("/",1)[-1], token[7:], self.headers.get("Idempotency-Key", ""), body)
                return self.respond(200 if result["replayed"] else 201, result)
            if path == "/api/setup":
                security.authorize_bootstrap(sys.modules[__name__],body)
                audit.begin(self,{'id':'admin'},body)
                set_password("admin", body.get("password"), first_setup=True)
                return self.respond(201, {"ok": True})
            if path == "/api/login":
                uid = body.get("user", "admin")
                if not isinstance(uid, str) or uid not in ("admin", "operator", "viewer"):
                    raise ValueError("Geçersiz kullanıcı")
                if MODE != "mock":
                    attempt_key = (self.client_address[0], uid)
                    with SESSION_LOCK:
                        for k in list(LOGIN_ATTEMPTS):
                            LOGIN_ATTEMPTS[k] = [x for x in LOGIN_ATTEMPTS[k] if x > time.time() - 300]
                            if not LOGIN_ATTEMPTS[k]:
                                del LOGIN_ATTEMPTS[k]
                        attempts = LOGIN_ATTEMPTS.setdefault(attempt_key, [])
                        if len(attempts) >= 10 or len(LOGIN_ATTEMPTS) > 1000:
                            raise PermissionError("Çok fazla giriş denemesi; 5 dakika sonra tekrar dene")
                        attempts.append(time.time())
                    if not check_login(uid, body.get("token", "")) or not mfa.verify_login(sys.modules[__name__],uid,body.get("code", "")):
                        raise PermissionError("Rol veya parola hatalı")
                    with SESSION_LOCK:
                        LOGIN_ATTEMPTS.pop(attempt_key, None)
                audit.begin(self,{'id':uid})
                with connect() as login_db:audit.emit(login_db,'login_succeeded')
                sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
                with SESSION_LOCK:
                    expired = [k for k, v in SESSIONS.items() if v["expires"] < time.time()]
                    for k in expired:
                        SESSIONS.pop(k, None)
                    if len(SESSIONS) > 1000:
                        raise ValueError("Oturum limiti dolu")
                    SESSIONS[sid] = {"uid": uid, "csrf": csrf, "expires": time.time() + 28800}
                secure = "; Secure" if os.getenv("COOKIE_SECURE") == "1" else ""
                return self.respond(200, {"ok": True}, "harbor=" + sid + "; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800" + secure)
            user, session = self.user()
            audit.begin(self,user,body)
            with connect() as scope_db:access.preflight(scope_db,user,path,self.path,body)
            if access.handle_post(self,user,path,body,sys.modules[__name__]):return
            if mfa.handle_post(self,user,path,body,sys.modules[__name__]):
                return
            if mcp_registry.handle_post(self,user,path,body,sys.modules[__name__]):
                return
            if operations.handle_post(self,user,path,body,sys.modules[__name__]):
                return
            if lab.handle_post(self,user,path,body,sys.modules[__name__]):
                return
            if privacy.handle_post(self,user,path,body,sys.modules[__name__]):
                return
            if incidents.handle_post(self,user,path,body,sys.modules[__name__]):
                return
            if playbooks.handle_post(self,user,path,body,sys.modules[__name__]):
                return
            if platform.handle_post(self, user, path, body, sys.modules[__name__]):
                return
            if workspace.handle_post(self, user, path, body, sys.modules[__name__]):
                return
            if path == "/api/browser/install":
                if user["role"] != "admin":
                    raise PermissionError("MCP kurulumunu yalnızca yönetici başlatabilir")
                threading.Thread(target=install_browser, args=(DB_PATH,), daemon=True).start()
                return self.respond(202, {"ok": True})
            if path == "/api/agents":
                return self.respond(201, {"id": save_agent(user, body)})
            if path.startswith("/api/agents/") and path.endswith("/test"):
                return self.respond(200, test_agent(user, path.split("/")[-2]))
            if path.startswith("/api/agents/") and path.endswith("/toggle"):
                change_agent_state(user, path.split("/")[-2], enabled=body.get("enabled"))
                return self.respond(200, {"ok": True})
            if path.startswith("/api/agents/") and path.endswith("/delete"):
                change_agent_state(user, path.split("/")[-2], delete=True)
                return self.respond(200, {"ok": True})
            if path == "/api/history/delete":
                return self.respond(200, {"deleted": delete_history(user, body)})
            if path == "/api/access/password":
                if user["role"] != "admin":
                    raise PermissionError("Erişim ayarı için yönetici rolü gerekli")
                uid = body.get("user")
                if uid not in ("admin", "operator", "viewer"):
                    raise ValueError("Geçersiz rol")
                if security.public_mode():
                    with connect() as auth_db:
                        if not mfa.statuses(auth_db).get(uid,False):raise ValueError('Önce hedef hesap için iki adımlı doğrulama kur')
                set_password(uid, body.get("password"))
                return self.respond(200, {"ok": True, "reauthenticate": uid == user["id"]})
            if path == "/api/logout":
                with connect() as logout_db:audit.emit(logout_db,'logout')
                with SESSION_LOCK:
                    for key in [k for k, v in SESSIONS.items() if v is session]:
                        SESSIONS.pop(key, None)
                return self.respond(200, {"ok": True}, "harbor=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
            if path == "/api/tasks":
                return self.respond(201, {"id": create_task(user, body)})
            if path.startswith('/api/tasks/') and path.endswith('/followup'):
                return self.respond(201, {'id': platform.followup(sys.modules[__name__], user, path.split('/')[-2], body)})
            if path.startswith("/api/tasks/") and path.endswith("/retry"):
                tid = path.split("/")[-2]
                with connect() as db:
                    task = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
                    run = db.execute("SELECT * FROM runs WHERE task_id=? AND id=?", (tid, body.get("run_id"))).fetchone()
                    if not task or not run or task["status"] not in ("failed", "incomplete", "cancelled") or run["status"] not in ("failed", "incomplete"):
                        raise ValueError("Yalnızca bitmiş görevin başarısız/eksik adımı yeniden denenebilir")
                    request_state = run["request_state"]
                    file_ids = [r[0] for r in db.execute('SELECT file_id FROM task_files WHERE task_id=?', (tid,))]
                    next_body = {"prompt": task["prompt"], "agents": [run["agent_id"]], "mode": "single", "web_enabled": bool(task["web_enabled"]), "project_id": task["project_id"], "file_ids": file_ids, "language": body.get("language", task["language"])}
                next_body.update(mask_sensitive=bool(task["mask_sensitive"]),allow_unmasked_images=bool(task["allow_unmasked_images"]))
                next_body["mcp_servers"] = [s["id"] for s in json.loads(task["mcp_selection"])]
                next_id = create_task(user, next_body, retry_state=request_state)
                return self.respond(201, {"id": next_id})
            if path.startswith("/api/tasks/") and path.endswith("/continue"):
                tid = path.split("/")[-2]
                with connect() as db:
                    task = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
                    run = db.execute("SELECT * FROM runs WHERE id=? AND task_id=?", (body.get("run_id"), tid)).fetchone()
                    if not task or not run or run["status"] != "incomplete":
                        raise ValueError("Devam ettirilecek eksik yanıt bulunamadı")
                    previous = (task["continuation_context"] + "\n\n" if task["continuation_context"] else "") + (run["output"] or "")
                    saved_state = run["provider_state"] if not (run["output"] or "").strip() else ""
                    next_task = {"prompt": task["prompt"], "mode": "single", "agents": [run["agent_id"]], "web_enabled": bool(task["web_enabled"]) and not bool(saved_state), "project_id": task["project_id"], "file_ids": [r[0] for r in db.execute("SELECT file_id FROM task_files WHERE task_id=?", (tid,))], "language": body.get("language", task["language"])}
                next_task.update(mask_sensitive=bool(task["mask_sensitive"]),allow_unmasked_images=bool(task["allow_unmasked_images"]))
                next_task["mcp_servers"] = [] if saved_state else [s["id"] for s in json.loads(task["mcp_selection"])]
                return self.respond(201, {"id": create_task(user, next_task, continuation_context=previous if not saved_state else "", resume_state=saved_state)})
            if path.startswith("/api/approvals/"):
                decide(user, path.split("/")[-1], body.get("decision"))
                return self.respond(200, {"ok": True})
            if path.startswith("/api/tasks/") and path.endswith("/cancel"):
                tid = path.split("/")[-2]
                with connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    task = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
                    if not task:
                        return self.respond(404, {"error": "Görev bulunamadı"})
                    if user["role"] == "viewer" or (user["role"] != "admin" and task["owner_id"] != user["id"]):
                        raise PermissionError("Bu görevi iptal edemezsiniz")
                    if task["status"] not in ("queued", "running", "awaiting_approval"):
                        raise ValueError("Görev zaten sona ermiş")
                    # In-flight calls may still bill. Keep their reservation until they finish.
                    reserve = task["reserved"] if task["status"] == "running" else 0
                    db.execute("UPDATE tasks SET status='cancelled',reserved=?,updated_at=? WHERE id=?", (reserve, now(), tid))
                    db.execute("UPDATE approvals SET status='rejected',decided_by=?,decided_at=? WHERE task_id=? AND status='pending'", (user["id"], now(), tid))
                    log(db, tid, "cancelled", "İptal edildi; başlamış sağlayıcı çağrıları tamamlanabilir")
                return self.respond(200, {"ok": True})
            return self.respond(404, {"error": "Endpoint bulunamadı"})
        except operations.DispatchPaused as exc:
            self.respond(503,{'error':str(exc),'code':'dispatch_paused'})
        except PermissionError as exc:
            self.respond(403, {"error": str(exc)})
        except ValueError as exc:
            self.respond(400, {"error": str(exc) if not isinstance(exc, json.JSONDecodeError) else "Geçersiz JSON"})
        except (TypeError, KeyError):
            self.respond(400, {"error": "Geçersiz istek; seçimleri, bütçeyi ve alanları kontrol edin"})
        except Exception:
            self.respond(500, {"error": "Sunucu hatası"})


def main():
    if MODE not in ("mock", "live"):
        raise SystemExit("APP_MODE mock veya live olmalı")
    host, port = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "8080"))
    # Bind before recovery: a second launch must never alter an active server's tasks.
    try:
        httpd = security.BoundedHTTPServer((host, port), Handler)
    except OSError:
        raise SystemExit(f"{port} portu kullanılıyor. ECY Agent Harbor zaten açıksa http://localhost:{port} adresini aç.")
    os.umask(0o077)
    initialize()
    try:security.validate_public(sys.modules[__name__])
    except ValueError as exc:
        httpd.server_close();raise SystemExit(str(exc))
    if requires_setup():
        print('Yerel tek kullanımlık kurulum kodu / Local bootstrap code: '+security.bootstrap_token(sys.modules[__name__]),flush=True)
    threading.Thread(target=automation.loop, args=(sys.modules[__name__],), daemon=True).start()
    for _ in range(WORKERS):
        threading.Thread(target=worker, daemon=True).start()
    print(f"ECY Agent Harbor ({MODE}): http://{host}:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        STOP.set()
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
