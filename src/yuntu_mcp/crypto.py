"""与 Go 版 utils/crypto.go 对齐的加解密/哈希工具。

- API Key 明文以 AES-256-GCM 加密落库，密钥由 API_KEY_ENCRYPT_SECRET 的 SHA-256 派生。
- 密文格式：base64url(nonce || ciphertext)（Raw URL encoding，无填充）。
- api_keys 表按明文 SHA-256 十六进制哈希（key_hash）检索，不落明文。
- 渠道 upstream_configs.api_key 用同一密钥加密，读取时解密；解密失败视为明文保留原值。
"""
import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_SIZE = 12


def _api_key_encrypt_key() -> bytes:
    secret = os.environ.get("API_KEY_ENCRYPT_SECRET", "").strip()
    if not secret or len(secret) < 16:
        raise RuntimeError(
            "环境变量 API_KEY_ENCRYPT_SECRET 未配置或长度不足（至少 16 字符），拒绝使用弱加密密钥"
        )
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _b64url_decode(s: str) -> bytes:
    # Go 使用 base64.RawURLEncoding（无填充），Python 需要补齐 padding
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def encrypt_api_key(plaintext: str) -> str:
    key = _api_key_encrypt_key()
    nonce = os.urandom(_NONCE_SIZE)
    sealed = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return _b64url_encode(nonce + sealed)


def decrypt_api_key(ciphertext: str) -> str:
    key = _api_key_encrypt_key()
    raw = _b64url_decode(ciphertext)
    if len(raw) < _NONCE_SIZE:
        raise ValueError("密文长度无效")
    nonce, data = raw[:_NONCE_SIZE], raw[_NONCE_SIZE:]
    plain = AESGCM(key).decrypt(nonce, data, None)
    return plain.decode("utf-8")


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()