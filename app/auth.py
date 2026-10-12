"""Password hashing and signed session cookies. Stdlib only - no external crypto deps."""
import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import Request

from . import config

SECRET = config.SECRET_KEY.encode()
COOKIE_NAME = "nfc_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14 days

PBKDF2_ROUNDS = 240_000


# --------------------------------------------------------------------------- #
# passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
    return hmac.compare_digest(dk.hex(), hash_hex)


# --------------------------------------------------------------------------- #
# session cookie: base64(json).signature
# --------------------------------------------------------------------------- #
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_session(payload: dict) -> str:
    body = _b64e(json.dumps({**payload, "iat": int(time.time())}, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_session(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    body, _, sig = token.rpartition(".")
    expected = _b64e(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(time.time()) - int(payload.get("iat", 0)) > SESSION_MAX_AGE:
        return None
    return payload


def session_from_request(request: Request) -> dict | None:
    return read_session(request.cookies.get(COOKIE_NAME))


def hash_token(token: str) -> str:
    """Hash for reset tokens. Plain SHA-256 is right here: the token is already 32
    random bytes, so there is nothing to brute-force and no need to slow lookups down."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(nbytes: int = 8) -> str:
    """Short, URL-safe, unguessable token for a tag."""
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"  # no look-alikes, easier to read off a card
    return "".join(secrets.choice(alphabet) for _ in range(nbytes))
