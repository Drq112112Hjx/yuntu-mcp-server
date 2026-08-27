"""MCP Streamable HTTP 模式端到端测试。

启动子进程（MCP_TRANSPORT=http），按 MCP Streamable HTTP 协议调 /mcp 端点：
initialize(拿 session id) → notifications/initialized → tools/list → tools/call。
运行：.venv/Scripts/python tests/test_http.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import requests

from yuntu_mcp import crypto
from yuntu_mcp import db as dbmod


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _seed_db(path):
    db = dbmod.Database("sqlite:///" + path)
    for ddl in [
        "CREATE TABLE api_keys (id INTEGER PRIMARY KEY, key TEXT, key_hash TEXT UNIQUE, tenant_id TEXT, enabled INTEGER, expires_at TEXT)",
        "CREATE TABLE upstream_configs (id INTEGER PRIMARY KEY, name TEXT, endpoint TEXT, api_path TEXT, api_key TEXT, models TEXT, models_detail TEXT, status INTEGER, weight INTEGER)",
        "CREATE TABLE tenant_balances (id INTEGER PRIMARY KEY, tenant_id TEXT UNIQUE, balance REAL, token_balance INTEGER)",
        "CREATE TABLE tenant_capabilities (id INTEGER PRIMARY KEY, tenant_id TEXT UNIQUE, allowed_models TEXT, allowed_abilities TEXT)",
    ]:
        db.execute(ddl)
    api_key = "test-platform-key-003"
    db.execute("INSERT INTO api_keys (key,key_hash,tenant_id,enabled,expires_at) VALUES (%s,%s,%s,%s,%s)",
               (api_key, crypto.hash_api_key(api_key), "tenant_1", 1, None))
    db.execute("INSERT INTO upstream_configs (name,endpoint,api_path,api_key,models,models_detail,status,weight) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
               ("mock", "https://mock.invalid", "/v1/chat/completions", "ck", "deepseek-v4-flash", "", 1, 10))
    db.execute("INSERT INTO tenant_balances (tenant_id,balance,token_balance) VALUES (%s,%s,%s)", ("tenant_1", 0, 8888))
    db.execute("INSERT INTO tenant_capabilities (tenant_id,allowed_models,allowed_abilities) VALUES (%s,%s,%s)", ("tenant_1", "", ""))
    return api_key


def _post(url, payload, session_id=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    r = requests.post(url, json=payload, headers=headers, timeout=15)
    sid = r.headers.get("Mcp-Session-Id")
    # 必须用 r.content 按 UTF-8 解码：text/event-stream 无 charset 时 requests 会误判为 latin-1，
    # 导致中文工具描述乱码并破坏 JSON 结构
    body = r.content.decode("utf-8")
    messages = []
    # 统一换行符为 \n（FastMCP SSE 帧用 \r\n\r\n 分隔）
    text = body.replace("\r\n", "\n").strip()
    # 单帧直接返回 JSON（Content-Type: application/json）时直接解析
    if text.startswith("{"):
        try:
            messages.append(json.loads(text))
        except ValueError:
            pass
        return r.status_code, sid, messages
    # 否则按 SSE 解析 data: 帧
    for block in text.split("\n\n"):
        data = None
        for line in block.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
        if data and data != "[DONE]":
            try:
                messages.append(json.loads(data))
            except ValueError:
                pass
    return r.status_code, sid, messages


def main():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    api_key = _seed_db(path)
    port = _free_port()
    base = "http://127.0.0.1:%d" % port

    env = dict(os.environ)
    env["DB_DSN"] = "sqlite:///" + path
    env["API_KEY_ENCRYPT_SECRET"] = "smoke-test-secret-0123456789ab"
    env["MCP_API_KEY"] = api_key
    env["MCP_TRANSPORT"] = "http"
    env["MCP_PORT"] = str(port)

    proc = subprocess.Popen(
        [sys.executable, "-m", "yuntu_mcp"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    try:
        # 等待服务就绪
        url = base + "/mcp"
        last = None
        for _ in range(40):
            try:
                requests.post(url, json={"jsonrpc": "2.0", "id": 0, "method": "ping"}, timeout=1)
                break
            except Exception as e:
                last = e
                time.sleep(0.25)
        else:
            raise RuntimeError("服务未就绪: %s" % last)

        # initialize
        code, sid, msgs = _post(url, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "http-smoke", "version": "0.0.1"}}})
        assert code == 200 and sid, (code, sid, msgs)
        assert msgs[0]["result"]["protocolVersion"], msgs
        print("initialize OK, session_id=", sid[:8], "...")

        # notifications/initialized（Streamable HTTP 规范：notification 返回 202）
        code, _, msgs = _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, sid)
        assert code in (200, 202), code

        # tools/list
        code, _, msgs = _post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sid)
        if not msgs:
            raise AssertionError("tools/list empty msgs")
        names = [t["name"] for t in msgs[0]["result"]["tools"]]
        assert names == ["chat_completion", "get_balance", "list_models"], names
        print("tools/list OK:", names)

        # get_balance
        code, _, msgs = _post(url, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                    "params": {"name": "get_balance", "arguments": {}}}, sid)
        payload = json.loads(msgs[0]["result"]["content"][0]["text"])
        assert payload["token_balance"] == 8888 and payload["tenant_id"] == "tenant_1", payload
        print("get_balance OK:", payload)

        # list_models
        code, _, msgs = _post(url, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                    "params": {"name": "list_models", "arguments": {}}}, sid)
        lp = json.loads(msgs[0]["result"]["content"][0]["text"])
        assert lp["total"] == 1, lp
        print("list_models OK:", lp)

        # chat_completion（无可达渠道 → 预期返回错误）
        code, _, msgs = _post(url, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                    "params": {"name": "chat_completion",
                                               "arguments": {"messages": '[{"role":"user","content":"hi"}]',
                                                             "model": "nonexistent"}}}, sid)
        cp = json.loads(msgs[0]["result"]["content"][0]["text"])
        assert "error" in cp, cp
        print("chat_completion (reject/no-channel) OK:", cp)

        print("STDIO-HANDLER HTTP TEST PASSED")
    finally:
        proc.kill()


if __name__ == "__main__":
    main()