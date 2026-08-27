"""运行时配置：从环境变量加载，缺失时给出明确中文指引并退出。"""
import os


class Config:
    def __init__(self) -> None:
        self.db_dsn = os.environ.get("DB_DSN", "")
        self.api_key_encrypt_secret = os.environ.get("API_KEY_ENCRYPT_SECRET", "")
        # 可托管（uvx/魔搭）场景下 API Key 通过运行时环境变量注入（按租户各自配置）。
        # HTTP 传输下也读取同一变量，保持与 stdio 一致。
        self.mcp_api_key = os.environ.get("MCP_API_KEY", "") or os.environ.get("YUNTU_API_KEY", "")
        self.transport = (os.environ.get("MCP_TRANSPORT", "stdio") or "stdio").strip().lower()
        self.host = os.environ.get("MCP_HOST", "0.0.0.0")
        try:
            self.port = int(os.environ.get("MCP_PORT", "8081"))
        except ValueError:
            self.port = 8081


def load_config() -> Config:
    cfg = Config()
    missing = []
    if not cfg.db_dsn:
        missing.append("DB_DSN")
    if len(cfg.api_key_encrypt_secret.strip()) < 16:
        missing.append("API_KEY_ENCRYPT_SECRET（至少 16 字符，需与云途知汇主平台一致）")

    if missing:
        lines = [
            "MCP Server 环境变量缺失，启动中止。缺失项: %s" % ", ".join(missing),
            "请先配置以下环境变量：",
            "  DB_DSN                    数据库连接串，示例: user:pass@tcp(host:3306)/db?charset=utf8mb4&parseTime=True&loc=Local，或 sqlite:///path",
            "  API_KEY_ENCRYPT_SECRET    API Key 加密密钥（至少16字符），需与云途知汇主平台一致",
            "  MCP_API_KEY               当前租户的 API Key（可选，缺失时 get_balance/list_models 返回空、chat_completion 拒绝）",
            "  MCP_TRANSPORT             stdio 或 http（默认 stdio，uvx 托管走 stdio）",
            "  MCP_PORT                  HTTP 传输时监听端口（默认 8081）",
        ]
        raise SystemExit("\n".join(lines))
    return cfg