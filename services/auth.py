"""Kalshi request signing. Contract: presign text, key handling, headers.

Presign text is `<timestamp_ms><METHOD><path>` with the path stripped of
query params. Ed25519 keys sign the text directly; RSA keys use RSA-PSS
with SHA-256 (MGF1-SHA256, salt length 32). Output is base64.
"""

import base64
import os
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Credentials:
    key_id: str
    private_key_pem: str
    env: str = "demo"  # demo | prod


def load_credentials(env: str = "demo") -> Credentials | None:
    """Contract: creds come only from env, never from code. None = poll mode."""
    key_id = os.environ.get("KALSHI_KEY_ID", "")
    pem = os.environ.get("KALSHI_PRIVATE_KEY", "")
    pem_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
    if pem_path and not pem:
        with open(pem_path, encoding="utf-8") as f:
            pem = f.read()
    if not key_id or not pem:
        return None
    return Credentials(key_id=key_id, private_key_pem=pem, env=env)


def presign_text(timestamp_ms: int, method: str, path: str) -> bytes:
    clean_path = path.split("?", 1)[0]
    return f"{timestamp_ms}{method.upper()}{clean_path}".encode()


def sign_request(timestamp_ms: int, method: str, path: str, private_key_pem: str) -> str:
    """Contract: Ed25519 signs directly, RSA uses PSS-SHA256. Returns base64."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    msg = presign_text(timestamp_ms, method, path)
    if isinstance(key, ed25519.Ed25519PrivateKey):
        sig = key.sign(msg)
    elif isinstance(key, rsa.RSAPrivateKey):
        sig = key.sign(
            msg,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256.digest_size,
            ),
            hashes.SHA256(),
        )
    elif isinstance(key, ec.EllipticCurvePrivateKey):  # pragma: no cover
        raise ValueError("ECDSA keys are not supported by Kalshi")
    else:  # pragma: no cover
        raise ValueError(f"unsupported key type: {type(key)}")
    return base64.b64encode(sig).decode()


def auth_headers(method: str, path: str, creds: Credentials) -> dict[str, str]:
    """Contract: header names and timestamp unit (ms) Kalshi expects."""
    ts = int(time.time() * 1000)
    return {
        "KALSHI-ACCESS-KEY": creds.key_id,
        "KALSHI-ACCESS-TIMESTAMP": str(ts),
        "KALSHI-ACCESS-SIGNATURE": sign_request(ts, method, path, creds.private_key_pem),
    }


WS_URLS = {
    "prod": "wss://external-api-ws.kalshi.com/trade-api/ws/v2",
    "demo": "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2",
}
WS_PATH = "/trade-api/ws/v2"
