"""端到端冒烟测试：用临时 SQLite 校验三个工具的核心逻辑。

运行：.venv/Scripts/python -m pytest tests/test_smoke.py -v
（或直接 .venv/Scripts/python tests/test_smoke.py）
"""
import json
import os
import tempfile

# 必须在导入 server 前设置环境变量（crypto/config 读取）
os.environ["API_KEY_ENCRYPT_SECRET"] = "smoke-test-secret-0123456789ab"
os.environ["MCP_API_KEY"] = "test-platform-key-001"
os.environ["MCP_TRANSPORT"] = "stdio"

from yuntu_mcp import crypto
from yuntu_mcp import db as dbmod
from yuntu_mcp import server


def _init_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    dsn = "sqlite:///" + path
    db = dbmod.Database(dsn)
    db.execute(
        "CREATE TABLE api_keys (id INTEGER PRIMARY KEY, key TEXT, key_hash TEXT UNIQUE, "
        "tenant_id TEXT, enabled INTEGER, expires_at TEXT)"
    )
    db.execute(
        "CREATE TABLE upstream_configs (id INTEGER PRIMARY KEY, name TEXT, endpoint TEXT, "
        "api_path TEXT, api_key TEXT, models TEXT, models_detail TEXT, status INTEGER, weight INTEGER)"
    )
    db.execute(
        "CREATE TABLE tenant_balances (id INTEGER PRIMARY KEY, tenant_id TEXT UNIQUE, "
        "balance REAL, token_balance INTEGER)"
    )
    db.execute(
        "CREATE TABLE tenant_capabilities (id INTEGER PRIMARY KEY, tenant_id TEXT UNIQUE, "
        "allowed_models TEXT, allowed_abilities TEXT)"
    )
    return db, path


def _seed(db):
    key = os.environ["MCP_API_KEY"]
    db.execute(
        "INSERT INTO api_keys (key, key_hash, tenant_id, enabled, expires_at) VALUES (%s,%s,%s,%s,%s)",
        (key, crypto.hash_api_key(key), "tenant_1", 1, None),
    )
    db.execute(
        "INSERT INTO upstream_configs (name, endpoint, api_path, api_key, models, models_detail, status, weight) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("mock-chan", "https://mock.invalid", "/v1/chat/completions", "chan-key", "deepseek-v4-flash,gpt-4o",
         "", 1, 10),
    )
    db.execute(
        "INSERT INTO tenant_balances (tenant_id, balance, token_balance) VALUES (%s,%s,%s)",
        ("tenant_1", 0, 5000),
    )
    db.execute(
        "INSERT INTO tenant_capabilities (tenant_id, allowed_models, allowed_abilities) VALUES (%s,%s,%s)",
        ("tenant_1", "", ""),
    )


def _install(db, path):
    server._db = db
    server._config = type("Cfg", (), {
        "mcp_api_key": os.environ["MCP_API_KEY"],
    })()
    return path


def test_get_balance():
    db, path = _init_db()
    _seed(db)
    _install(db, path)
    out = server.get_balance()
    payload = json.loads(out)
    assert payload["tenant_id"] == "tenant_1"
    assert payload["token_balance"] == 5000
    assert payload["balance"] == 0


def test_get_balance_invalid_key():
    db, path = _init_db()
    _seed(db)
    server._db = db
    server._config = type("Cfg", (), {"mcp_api_key": "wrong-key"})()
    payload = json.loads(server.get_balance())
    assert payload["balance"] == 0
    assert payload["message"]


def test_list_models():
    db, path = _init_db()
    _seed(db)
    _install(db, path)
    out = server.list_models()
    payload = json.loads(out)
    assert payload["total"] == 2
    ids = {m["id"] for m in payload["models"]}
    assert ids == {"deepseek-v4-flash", "gpt-4o"}


def test_chat_completion_no_channel():
    db, path = _init_db()
    _seed(db)
    _install(db, path)
    # 请求一个渠道不支持、也不匹配任何候选的模型 → 无可用渠道
    out = server.chat_completion('[{"role":"user","content":"hi"}]', model="nonexistent-model")
    payload = json.loads(out)
    assert "error" in payload


def test_chat_completion_success_and_deduct():
    db, path = _init_db()
    _seed(db)
    _install(db, path)

    def _fake_call(candidates, model, messages, temp):
        return {"content": "你好", "model": model, "input_tokens": 10, "output_tokens": 20,
                "total_tokens": 30}, None

    original = server.call_chat_completion
    server.call_chat_completion = _fake_call
    try:
        out = server.chat_completion('[{"role":"user","content":"hi"}]')
    finally:
        server.call_chat_completion = original
    payload = json.loads(out)
    assert payload["content"] == "你好"
    assert payload["total_tokens"] == 30
    # 校验已扣实际 Token
    b = dbmod.get_tenant_balance(db, "tenant_1")
    assert b["token_balance"] == 5000 - 30


if __name__ == "__main__":
    test_get_balance()
    test_get_balance_invalid_key()
    test_list_models()
    test_chat_completion_no_channel()
    test_chat_completion_success_and_deduct()
    print("ALL SMOKE TESTS PASSED")