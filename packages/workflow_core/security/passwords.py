from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import secrets
import sys

PASSWORD_HASH_SCHEME = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 310_000
DEFAULT_SALT_BYTES = 16


def hash_password(
    password: str,
    *,
    iterations: int = DEFAULT_ITERATIONS,
    salt: bytes | None = None,
) -> str:
    if not password:
        raise ValueError("password must not be empty")
    if iterations < 100_000:
        raise ValueError("iterations must be at least 100000")
    salt_bytes = salt or secrets.token_bytes(DEFAULT_SALT_BYTES)
    digest = _derive(password, salt_bytes, iterations)
    return "$".join(
        [
            PASSWORD_HASH_SCHEME,
            str(iterations),
            _b64encode(salt_bytes),
            _b64encode(digest),
        ]
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        scheme, iterations_text, salt_text, expected_text = password_hash.split("$", 3)
        if scheme != PASSWORD_HASH_SCHEME:
            return False
        iterations = int(iterations_text)
        salt = _b64decode(salt_text)
        expected = _b64decode(expected_text)
    except (ValueError, TypeError):
        return False
    actual = _derive(password, salt, iterations)
    return hmac.compare_digest(actual, expected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a PBKDF2 password hash for AGENT_WORKFLOW_AUTH_USERS_JSON.")
    parser.add_argument("password", nargs="?", help="Password to hash. Reads from stdin when omitted.")
    args = parser.parse_args(argv)

    password = args.password
    if password is None:
        password = sys.stdin.readline().rstrip("\n")
    print(hash_password(password))
    return 0


def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


if __name__ == "__main__":
    raise SystemExit(main())
