from __future__ import annotations

import base64
import getpass
import hashlib
import json
import secrets


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def prompt_user(role: str) -> dict[str, object]:
    username = input(f"{role} username: ").strip().lower()
    password = getpass.getpass(f"{role} password: ")
    salt = secrets.token_urlsafe(18)
    iterations = 250000
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
        dklen=32,
    )
    return {
        "username": username,
        "role": role,
        "salt": salt,
        "hash": b64url(digest),
        "iterations": iterations,
    }


def main() -> int:
    print("Enter viewer credentials:")
    viewer = prompt_user("viewer")
    print("Enter admin credentials:")
    admin = prompt_user("admin")
    print(json.dumps([viewer, admin]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
