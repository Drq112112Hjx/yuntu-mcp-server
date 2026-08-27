"""MCP stdio 握手冒烟测试：启动子进程，走 JSON-RPC 初始化并调用工具。
运行：.venv/Scripts/python tests/test_stdio.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

from yuntu_mcp import crypto
from yuntu_mcp import db as dbmod


def _seed_db(path):
    db = dbmod.Database("sqlite:///" + path)
    for ddl in [
        "CREATE TABLE api_keys (id INTEGER PRIMARY KEY, key TEXT, key_hash TEXT UNIQUE, tenant_id TEXT, enabled INTEGER, expires_at TEXT)",
        "CREATE TABLE upstream_configs (id INTEGER PRIMARY KEY, name TEXT, endpoint TEXT, api_path TEXT, api_key TEXT, models TEXT, models_detail TEXT, status INTEGER, weight INTEGER)",
        "CREATE TABLE tenant_balances (id INTEGER PRIMARY KEY, tenant_id TEXT UNIQUE, balance REAL, token_balance INTEGER)",
        "CREATE TABLE tenant_capabilities (id INTEGER PRIMARY KEY, tenant_id TEXT UNIQUE, allowed_models TEXT, allowed_abilities TEXT)",
    ]:
        db.execute(ddl)
    api_key = "test-platform-key-002"
    db.execute("INSERT INTO api_keys (key,key_hash,tenant_id,enabled,expires_at) VALUES (%s,%s,%s,%s,%s)",
               (api_key, crypto.hash_api_key(api_key), "tenant_1", 1, None))
    db.execute("INSERT INTO upstream_configs (name,endpoint,api_path,api_key,models,models_detail,status,weight) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
               ("mock", "https://mock.invalid", "/v1/chat/completions", "ck", "deepseek-v4-flash", "", 1, 10))
    db.execute("INSERT INTO tenant_balances (tenant_id,balance,token_balance) VALUES (%s,%s,%s)", ("tenant_1", 0, 8888))
    db.execute("INSERT INTO tenant_capabilities (tenant_id,allowed_models,allowed_abilities) VALUES (%s,%s,%s)", ("tenant_1", "", ""))
    return api_key


def main():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    api_key = _seed_db(path)

    env = dict(os.environ)
    env["DB_DSN"] = "sqlite:///" + path
    env["API_KEY_ENCRYPT_SECRET"] = "smoke-test-secret-0123456789ab"
    env["MCP_API_KEY"] = api_key
    env["MCP_TRANSPORT"] = "stdio"

    proc = subprocess.Popen(
        [sys.executable, "-m", "yuntu_mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env,
    )

    def send(obj):
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def expect(limit=10):
        for _ in range(limit):
            line = proc.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            if "id" in msg:
                return msg
        return None

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "smoke", "version": "0.0.1"}}})
        init = expect()
        assert init and "result" in init, init
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # tools/list
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = expect()
        names = [t["name"] for t in tools["result"]["tools"]]
        assert names == ["chat_completion", "get_balance", "list_models"], names

        # get_balance
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "get_balance", "arguments": {}}})
        bal = expect()
        text = bal["result"]["content"][0]["text"]
        payload = json.loads(text)
        assert payload["token_balance"] == 8888 and payload["tenant_id"] == "tenant_1", payload

        # list_models
        send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "list_models", "arguments": {}}})
        lm = expect()
        lp = json.loads(lm["result"]["content"][0]["text"])
        assert lp["total"] == 1, lp

        print("STDIO HANDSHAKE OK, tools=", names)
    finally:
        try:
            proc.kill()
        except Exception:
            pass


if __name__ == "__main__":
    main()