"""Compatibility fixes for the ``mercari`` package's DPoP requests."""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Callable

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
import mercari.mercari as _mercari

_RETRY_STATUS_CODES = (401, 429, 500, 502, 503)
_RETRY_DELAYS_SECONDS = (1, 2, 4, 8)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _int_to_b64url(value: int) -> str:
    return _b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def _generate_dpop(*, uuid: str, method: str, url: str) -> str:
    """Generate a JOSE-compliant ES256 DPoP token with padded signature halves."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    numbers = private_key.public_key().public_numbers()
    header = {"typ": "dpop+jwt", "alg": "ES256", "jwk": {
        "crv": "P-256", "kty": "EC", "x": _int_to_b64url(numbers.x),
        "y": _int_to_b64url(numbers.y),
    }}
    payload = {"iat": int(time.time()), "jti": uuid, "htu": url, "htm": method.upper()}
    signing_input = "{}.{}".format(_b64url(json.dumps(header).encode("utf-8")), _b64url(json.dumps(payload).encode("utf-8")))
    r, s = utils.decode_dss_signature(private_key.sign(signing_input.encode("utf-8"), ec.ECDSA(hashes.SHA256())))
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return "{}.{}".format(signing_input, _b64url(signature))


def _fetch(url: str, data: dict, parser: Callable[[Any], Any], method: str = "POST"):
    """The upstream fetch helper with a fresh DPoP token for bounded retries."""
    last_error: Exception | None = None
    for delay in (*_RETRY_DELAYS_SECONDS, None):
        headers = {
            "DPOP": _generate_dpop(uuid="Mercari Python Bot", method=method, url=url),
            "X-Platform": "web", "Accept": "*/*", "Accept-Encoding": "deflate, gzip",
            "Content-Type": "application/json; charset=utf-8", "User-Agent": "python-mercari",
        }
        if method == "POST":
            response = requests.post(url, headers=headers, data=json.dumps(data, ensure_ascii=False).encode("utf-8"), timeout=30)
        else:
            response = requests.get(url, headers=headers, params=_mercari.convert_booleans(data), timeout=30)
        if response.status_code == 200:
            return parser(response.json())
        last_error = requests.HTTPError(f"{response.status_code} for url: {url}", response=response)
        if response.status_code not in _RETRY_STATUS_CODES:
            raise last_error
        if delay is not None:
            time.sleep(delay)
    raise last_error  # type: ignore[misc]


_mercari.generate_DPOP = _generate_dpop
_mercari.fetch = _fetch
