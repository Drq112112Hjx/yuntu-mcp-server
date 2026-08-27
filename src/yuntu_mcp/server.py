"""FastMCP Server 入口：三个工具（chat_completion / get_balance / list_models）+ 双传输 main。

- 默认 stdio 传输（uvx / 魔搭托管拉起），可用 MCP_TRANSPORT=http 切换到 Streamable HTTP。
- API Key 鉴权：读取运行时环境变量 MCP_API_KEY（或 YUNTU_API_KEY），
  按 key_hash 查 api_keys 表识别租户；未配置/无效时相应工具拒绝或返回空。
- 价格与折扣逻辑暂不包含：chat_completion 仅做「余额>0 检查 + 扣实际 Token 数」。
"""
import json
import logging
from datetime import datetime

from fastmcp import FastMCP

from yuntu_mcp import db as dbmod
from yuntu_mcp.channels import call_chat_completion, load_channels, select_candidates
from yuntu_mcp.config import Config, load_config
from yuntu_mcp.crypto import hash_api_key

logger = logging.getLogger("yuntu_mcp")

mcp = FastMCP("云途知汇算力调度")

# 全局单例依赖（对齐 Go 版 main.go 中的全局依赖）
_config: Config = None
_db: dbmod.Database = None


def resolve_api_key() -> str:
    return _config.mcp_api_key if _config else ""


def _parse_json_list(raw):
    if not raw:
        return None  # 空字符串 → 未配置限制，全部允许
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else None
    except Exception:
        return None


def tenant_from_key(api_key: str):
    """按 API Key 识别租户。返回 (tenant_id, error)。"""
    if not api_key:
        return None, "未提供 API Key"
    row = dbmod.get_api_key_by_hash(_db, hash_api_key(api_key))
    if not row:
        return None, "API Key 无效"
    if not row.get("enabled"):
        return None, "API Key 已被禁用"
    expires = row.get("expires_at")
    if expires is not None:
        try:
            if isinstance(expires, str):
                expires_dt = datetime.strptime(expires[:19], "%Y-%m-%d %H:%M:%S")
            else:
                expires_dt = expires
            if expires_dt.replace(tzinfo=None) < datetime.now():
                return None, "API Key 已过期"
        except (ValueError, TypeError):
            pass
    tenant_id = row.get("tenant_id") or ""
    if not tenant_id:
        return None, "API Key 未关联租户"
    return tenant_id, None


def is_model_allowed(tenant_id: str, model: str) -> bool:
    cap = dbmod.get_tenant_capability(_db, tenant_id)
    if not cap:
        return True  # 未配置默认允许
    allowed = _parse_json_list(cap.get("allowed_models"))
    if allowed is None:
        return True  # 空列表/空串 → 全部允许
    return model in allowed


def has_balance(tenant_id: str) -> bool:
    b = dbmod.get_tenant_balance(_db, tenant_id)
    if not b:
        return False
    return int(b.get("token_balance") or 0) > 0 or float(b.get("balance") or 0) > 0


def _json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


# ===== 工具定义（对齐 Go 版 MCP tool）=====

@mcp.tool
def chat_completion(messages: str, model: str = "deepseek-v4-flash", temperature: float = 0.7) -> str:
    """调用大模型进行对话，从渠道管理中选择可用渠道并自动扣除 Token。"""
    tenant_id, err = tenant_from_key(resolve_api_key())
    if err:
        return _json({"error": err})

    try:
        msgs = json.loads(messages) if isinstance(messages, str) else messages
    except ValueError:
        return _json({"error": "messages 格式错误"})
    if not isinstance(msgs, list):
        return _json({"error": "messages 格式错误"})

    if not is_model_allowed(tenant_id, model):
        return _json({"error": "模型 %s 未被允许调用，请联系管理员" % model})
    if not has_balance(tenant_id):
        return _json({"error": "余额不足，请充值"})

    channels = load_channels(_db)
    candidates = select_candidates(channels, model)
    if not candidates:
        return _json({"error": "无可用的渠道"})

    result, last_error = call_chat_completion(candidates, model, msgs, temperature)
    if not result:
        return _json({"error": "所有渠道均调用失败: %s" % last_error})

    # 精简版扣费：扣实际 Token 数（价格/折扣逻辑暂不包含）
    if result["total_tokens"] > 0:
        dbmod.deduct_token_balance(_db, tenant_id, result["total_tokens"])

    return _json(result)


@mcp.tool
def get_balance() -> str:
    """查询当前租户的 Token 余额（需配置 MCP_API_KEY 识别租户）。"""
    tenant_id, err = tenant_from_key(resolve_api_key())
    if err:
        return _json({"balance": 0, "token_balance": 0, "message": err})

    b = dbmod.get_tenant_balance(_db, tenant_id)
    balance = float(b.get("balance") or 0) if b else 0.0
    token_balance = int(b.get("token_balance") or 0) if b else 0
    return _json({"balance": balance, "token_balance": token_balance, "tenant_id": tenant_id})


@mcp.tool
def list_models() -> str:
    """列出当前可用的所有模型列表。"""
    channels = load_channels(_db)
    seen = set()
    models = []
    for ch in channels:
        for m in ch.get_model_list():
            if not m or m in seen:
                continue
            seen.add(m)
            models.append({"id": m, "channel": ch.name, "endpoint": ch.endpoint})
    return _json({"models": models, "total": len(models)})


def main() -> None:
    global _config, _db
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _config = load_config()
    _db = dbmod.Database(_config.db_dsn)
    logger.info("MCP Server 初始化完成，传输模式: %s", _config.transport)

    if _config.transport == "http":
        logger.info("Streamable HTTP 已就绪: http://0.0.0.0:%s/mcp", _config.port)
        mcp.run(transport="streamable-http", host=_config.host, port=_config.port)
    else:
        logger.info("MCP STDIO 服务已就绪（uvx 托管模式）")
        mcp.run(transport="stdio")