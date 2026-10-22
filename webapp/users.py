"""Accounts of the web application (HTTP Basic authentication).

Passwords are stored as PBKDF2 hashes in webapp/users.txt (one "name:hash" per line).
Without any account the application is open (local use only).

  python -m webapp.users add <name>      # create / change a password (asked interactively)
  python -m webapp.users remove <name>
  python -m webapp.users list
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import os
import sys
from pathlib import Path

USERS_FILE = Path(os.environ.get("BULLETIN_USERS_FILE", Path(__file__).parent / "users.txt"))
ITERATIONS = 200_000


def _hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${h.hex()}"


def _verify(password: str, stored: str) -> bool:
    try:
        _, it, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), h)
    except ValueError:
        return False


def load_users() -> dict[str, str]:
    if not USERS_FILE.is_file():
        return {}
    users = {}
    for line in USERS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and ":" in line:
            name, h = line.split(":", 1)
            users[name] = h
    return users


def save_users(users: dict[str, str]):
    USERS_FILE.write_text("".join(f"{n}:{h}\n" for n, h in sorted(users.items())), encoding="utf-8")
    USERS_FILE.chmod(0o600)


_cache: dict[str, str] = {}


def check_basic(header: str | None, users: dict[str, str]) -> str | None:
    """User name if the Authorization header is valid."""
    if not header or not header.lower().startswith("basic "):
        return None
    if header in _cache:  # PBKDF2 is slow on purpose: remember accepted headers
        name = _cache[header]
        return name if name in users else None
    try:
        name, _, pw = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:
        return None
    if name in users and _verify(pw, users[name]):
        _cache[header] = name
        return name
    return None


def main(argv: list[str]):
    if len(argv) < 1 or argv[0] not in ("add", "remove", "list"):
        print(__doc__)
        return 1
    users = load_users()
    if argv[0] == "list":
        print("\n".join(users) or "(aucun compte : application ouverte)")
    elif argv[0] == "add":
        name = argv[1]
        pw = getpass.getpass(f"Mot de passe pour {name} : ")
        if pw != getpass.getpass("Confirmer : "):
            print("Les mots de passe ne correspondent pas.")
            return 1
        users[name] = _hash(pw)
        save_users(users)
        print(f"Compte {name} enregistré.")
    elif argv[0] == "remove":
        users.pop(argv[1], None)
        save_users(users)
        print(f"Compte {argv[1]} supprimé.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
