# yuntu-mcp-server

云途知汇算力调度 **MCP Server（可托管版本）**，用 Python FastMCP 实现，功能对齐
[Go 版 MCP Server](../mcp-server) 的三个工具：

- `chat_completion`：调用大模型对话，渠道路由 + 故障转移，返回 content 与 token 用量（扣实际 Token）
- `get_balance`：查询当前租户余额（token_balance / balance）
- `list_models`：列出当前可用模型

> 说明：本版本暂不包含「价格/折扣」逻辑，只做「余额 > 0 检查 + 扣实际 Token 数」，
> 先跑通托管链路。后续再接入定价/折扣。

## 运行方式

### 本地开发 / 单元测试

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/python tests/test_smoke.py        # 逻辑冒烟测试（SQLite）
```

### uvx 直接拉起（PyPI 发布后，魔搭托管）

先发布 PyPI，然后在环境变量中配置运行时参数，`uvx` 即可拉起：

```bash
uvx yuntu-mcp-server
```

### HTTP 模式（可选，对齐 Go 版 /mcp 路径）

```bash
MCP_TRANSPORT=http MCP_PORT=8081 uvx yuntu-mcp-server
# 监听 http://0.0.0.0:8081/mcp （Streamable HTTP）
```

## 环境变量

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `DB_DSN` | 是 | 数据库连接串。MySQL：`user:pass@tcp(host:3306)/db?charset=utf8mb4&parseTime=True&loc=Local`；本地也可用 `sqlite:///xx.db` |
| `API_KEY_ENCRYPT_SECRET` | 是 | API Key 加密密钥（≥16 字符），**需与云途知汇主平台一致**（用于解密渠道 API Key、检索租户） |
| `MCP_API_KEY` | 否 | 当前租户的 API Key（stdio 无 HTTP Header，用运行时环境变量注入识别租户；缺失时 `get_balance/list_models` 返回空、`chat_completion` 拒绝）。也兼容读 `YUNTU_API_KEY` |
| `MCP_TRANSPORT` | 否 | `stdio`（默认，uvx 托管走 stdio）或 `http` |
| `MCP_HOST` / `MCP_PORT` | 否 | HTTP 模式监听地址/端口，默认 `0.0.0.0:8081` |

## 鉴权口径

- 与 Go 版一致：`api_keys` 表按 `key_hash`（明文 SHA-256）检索，不落明文。
- 校验 `enabled`、`expires_at`，取 `tenant_id` 作为计费/能力判断主体。
- 渠道 `upstream_configs.api_key` 用同一加密密钥解密后调用上游（解密失败视为明文保留）。

## 发布到 PyPI

```bash
python -m pip install build
python -m build                              # 生成 dist/ 下的 wheel 与 sdist
python -m twine upload dist/*                # 需配置 PyPI 令牌
```

发布后验证：

```bash
DB_DSN="user:pass@tcp(host:3306)/db?charset=utf8mb4" \
API_KEY_ENCRYPT_SECRET="<与主平台一致>" \
MCP_API_KEY="<租户Key>" \
uvx yuntu-mcp-server
```