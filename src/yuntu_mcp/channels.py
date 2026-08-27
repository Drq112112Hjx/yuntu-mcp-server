"""渠道路由与上游调用：对齐 Go 版 upstream_config 模型 + SelectChannelCandidates + DoChannelRequestWithRetry。

本模块暂不包含熔断器/计费，只做核心链路：启用渠道筛选、按模型匹配、按权重排序、逐个尝试实现故障转移。
"""
import json

import requests

from yuntu_mcp import crypto
from yuntu_mcp import db as dbmod


class Channel:
    def __init__(self, row: dict) -> None:
        self.name = row.get("name") or ""
        self.endpoint = row.get("endpoint") or ""
        self.api_path = row.get("api_path") or ""
        self.api_key = row.get("api_key") or ""
        self.models = row.get("models") or ""
        self.models_detail = row.get("models_detail") or ""
        self.status = row.get("status")
        self.weight = row.get("weight") or 0

    def decrypt_api_key(self) -> None:
        if not self.api_key:
            return
        try:
            self.api_key = crypto.decrypt_api_key(self.api_key)
        except Exception:
            pass  # 解密失败视为明文，保留原值（对齐 Go decryptChannel）

    def full_url(self) -> str:
        if not self.api_path:
            return self.endpoint
        if self.endpoint.endswith(self.api_path):
            return self.endpoint
        return self.endpoint.rstrip("/") + self.api_path

    def _models_detail_list(self):
        if not self.models_detail:
            return []
        try:
            data = json.loads(self.models_detail)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def get_model_list(self):
        details = self._models_detail_list()
        if details:
            ids = [m.get("id", "") for m in details if isinstance(m, dict)]
            ids = [i for i in ids if i]
            if ids:
                return ids
        if self.models in ("", "*"):
            return []
        return [x.strip() for x in self.models.split(",") if x.strip() and x.strip() != "*"]

    def supports_model(self, model: str) -> bool:
        if not model:
            return True
        if self.models == "*":
            return True
        return model in self.get_model_list()

    def url_for_model(self, model: str) -> str:
        # 对齐 Go URLForModel：文本/视觉走渠道路径；视频/图片走模型自身 api_path
        for m in self._models_detail_list():
            if isinstance(m, dict) and m.get("id") == model:
                api_path = m.get("api_path") or ""
                mtype = m.get("type") or ""
                if api_path and mtype in ("video", "image"):
                    return _build_model_url(self.endpoint, api_path)
                return self.full_url()
        return self.full_url()


def _build_model_url(endpoint: str, api_path: str) -> str:
    base = endpoint.rstrip("/")
    api_path = api_path.lstrip("/")
    if not api_path.startswith("v"):
        return _extract_scheme_host(base) + "/" + api_path
    version_prefix = api_path.split("/", 1)[0] if "/" in api_path else ""
    if base.endswith(api_path):
        return base
    suffix = api_path[len(version_prefix) + 1:] if version_prefix else api_path
    if version_prefix and base.endswith("/" + version_prefix):
        return base + "/" + suffix
    marker = "/" + version_prefix + "/"
    if version_prefix and marker in base:
        idx = base.rfind(marker)
        return base[: idx + len(version_prefix) + 1] + suffix
    return base + "/" + api_path


def _extract_scheme_host(base: str) -> str:
    scheme_end = base.find("://")
    if scheme_end < 0:
        return base
    rest = base[scheme_end + 3:]
    path_start = rest.find("/")
    if path_start < 0:
        return base
    return base[: scheme_end + 3 + path_start]


def load_channels(database) -> list:
    rows = dbmod.get_available_channels(database)
    channels = [Channel(r) for r in rows]
    for ch in channels:
        ch.decrypt_api_key()
    return channels


def select_candidates(channels: list, model: str) -> list:
    candidates = [ch for ch in channels if ch.status == 1 and (not model or ch.supports_model(model))]
    if not candidates:
        candidates = channels
    candidates.sort(key=lambda c: c.weight, reverse=True)
    return candidates


def call_chat_completion(candidates: list, model_name: str, messages: list, temperature: float = 0.7, timeout: int = 60):
    payload = {
        "model": model_name,
        "messages": messages,
        "stream": False,
        "max_tokens": 4096,
        "temperature": temperature,
    }
    last_error = None
    for ch in candidates:
        url = ch.url_for_model(model_name)
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + ch.api_key}
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code != 200:
                last_error = "渠道 %s 返回错误: %d - %s" % (ch.name, resp.status_code, resp.text[:500])
                continue  # 故障转移，尝试下一个渠道
            data = resp.json()
            usage = data.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens") or 0)
            output_tokens = int(usage.get("completion_tokens") or 0)
            reply = ""
            choices = data.get("choices") or []
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message") or {}
                reply = msg.get("content") or ""
            if not reply:
                reply = data.get("content") or ""
            if reply:
                return {
                    "content": reply,
                    "model": model_name,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                }, None
            last_error = "渠道 %s 返回空响应" % ch.name
        except requests.RequestException as e:
            last_error = "渠道 %s 请求失败: %s" % (ch.name, e)
            continue
    return None, last_error