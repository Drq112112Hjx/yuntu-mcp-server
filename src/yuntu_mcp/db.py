"""数据库访问层：兼容 MySQL / SQLite DSN，并提供 MCP 工具所需查询。

对齐 Go 版 repositories（api_key_repo / upstream_config_repo / tenant_balance_repo /
tenant_capability_repo）中由 MCP Server 用到的查询。
"""
import re
import sqlite3
import threading

try:
    import pymysql
except ImportError:  # pragma: no cover - 仅在未安装 PyMySQL 且不使用 MySQL 时无关紧要
    pymysql = None


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._is_mysql = "@" in dsn and "tcp(" in dsn
        self._lock = threading.RLock()
        self._conn = None

    def connect(self):
        if self._conn is not None:
            return self._conn
        if not self._is_mysql:
            path = self._dsn.replace("sqlite:///", "").replace("sqlite://", "")
            self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
        else:
            if pymysql is None:
                raise RuntimeError("使用 MySQL 需要安装 PyMySQL 依赖")
            self._conn = pymysql.connect(
                autocommit=True,
                cursorclass=pymysql.cursors.DictCursor,
                **self._parse_mysql_dsn(self._dsn),
            )
        return self._conn

    @staticmethod
    def _parse_mysql_dsn(dsn: str) -> dict:
        m = re.match(
            r"^(?P<user>[^:@/]+)(?::(?P<pass>[^@]*))?@tcp\((?P<host>[^:()]+):(?P<port>\d+)\)/(?P<db>[^?]+)(?:\?(?P<params>.*))?$",
            dsn,
        )
        if not m:
            raise ValueError("无法解析 MySQL DSN: %s" % dsn)
        params = m.group("params") or ""
        charset = "utf8mb4"
        for kv in params.split("&"):
            if kv.startswith("charset="):
                charset = kv.split("=", 1)[1]
        return {
            "host": m.group("host"),
            "port": int(m.group("port")),
            "user": m.group("user"),
            "password": m.group("pass") or "",
            "database": m.group("db"),
            "charset": charset,
        }

    def _fmt(self, sql: str) -> str:
        # MySQL 使用 %s 占位符，SQLite 使用 ? ，调用方统一用 %s 风格
        return sql.replace("%s", "?") if not self._is_mysql else sql

    def query(self, sql: str, params=()):
        conn = self.connect()
        with self._lock:
            cur = conn.cursor()
            try:
                cur.execute(self._fmt(sql), tuple(params))
                rows = cur.fetchall()
                return [dict(r) for r in rows]
            finally:
                cur.close()

    def execute(self, sql: str, params=()):
        conn = self.connect()
        with self._lock:
            cur = conn.cursor()
            try:
                cur.execute(self._fmt(sql), tuple(params))
                affected = cur.rowcount
                if not self._is_mysql:
                    conn.commit()
                return affected
            finally:
                cur.close()


# ===== 以下函数对齐 Go repositories 中 MCP 用到的查询 =====

def get_api_key_by_hash(db: Database, key_hash: str):
    rows = db.query(
        "SELECT id, key_hash, tenant_id, enabled, expires_at FROM api_keys WHERE key_hash = %s LIMIT 1",
        (key_hash,),
    )
    return rows[0] if rows else None


def get_available_channels(db: Database):
    return db.query("SELECT * FROM upstream_configs WHERE status = 1")


def get_tenant_balance(db: Database, tenant_id: str):
    rows = db.query(
        "SELECT tenant_id, balance, token_balance FROM tenant_balances WHERE tenant_id = %s LIMIT 1",
        (tenant_id,),
    )
    return rows[0] if rows else None


def deduct_token_balance(db: Database, tenant_id: str, tokens: int) -> bool:
    # 精简版扣费：仅在 token_balance 充足时原子扣减，不足则返回 False 静默跳过
    affected = db.execute(
        "UPDATE tenant_balances SET token_balance = token_balance - %s "
        "WHERE tenant_id = %s AND token_balance >= %s",
        (tokens, tenant_id, tokens),
    )
    return affected > 0


def get_tenant_capability(db: Database, tenant_id: str):
    rows = db.query(
        "SELECT tenant_id, allowed_models FROM tenant_capabilities WHERE tenant_id = %s LIMIT 1",
        (tenant_id,),
    )
    return rows[0] if rows else None