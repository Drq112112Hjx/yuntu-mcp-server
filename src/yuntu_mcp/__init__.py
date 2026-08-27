"""云途知汇算力调度 MCP Server（可托管版本）。

功能对齐 backend/mcp-server（Go 版），提供三个工具：
- chat_completion：对话，渠道路由 + 余额检查 + 扣实际 Token
- get_balance：查询当前租户余额
- list_models：列出可用模型

价格/折扣逻辑暂不包含，先跑通托管链路。
"""

__version__ = "1.0.0"