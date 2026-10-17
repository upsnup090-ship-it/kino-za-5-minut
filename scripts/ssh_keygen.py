"""Генерация ed25519 SSH-ключа в формате OpenSSH без ssh-keygen.

ssh-keygen в песочнице недоступен (proot, apt сломан), а ключ нужен для
push в GitHub по SSH. Формат OpenSSH собран вручную: приватный ключ —
PEM-обёртка с base64 от структуры openssh-key-v1, публичный — one-line.

Запуск:
    python scripts/ssh_keygen.py [--out ~/.ssh/id_ed25519] [--comment "..."]
"""

from __future__ import annotations

import argparse
import base64
import os
import secrets
import struct
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def _ssh_string(data: bytes) -> bytes:
    """Поле SSH: 4-байтная длина big-endian + данные."""
    return struct.pack(">I", len(data)) + data


def _openssh_private_key(private: ed25519.Ed25519PrivateKey, comment: str) -> str:
    """Собирает приватный ключ в формате openssh-key-v1 (PEM, без пароля)."""
    raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    check = int.from_bytes(secrets.token_bytes(4), "big")

    # приватная часть: check, check, тип, pub, priv, comment, padding
    private_block = (
        struct.pack(">II", check, check)
        + _ssh_string(b"ssh-ed25519")
        + _ssh_string(public)
        + _ssh_string(raw + public)
        + _ssh_string(comment.encode())
    )
    block_size = 8
    pad = (-len(private_block)) % block_size
    private_block += bytes(range(1, pad + 1))

    blob = (
        b"openssh-key-v1\x00"
        + _ssh_string(b"none")          # ciphername
        + _ssh_string(b"none")          # kdfname
        + _ssh_string(b"")              # kdfoptions
        + struct.pack(">I", 1)          # number of keys
        + _ssh_string(
            _ssh_string(b"ssh-ed25519") + _ssh_string(public)
        )
        + _ssh_string(private_block)
    )

    b64 = base64.b64encode(blob).decode()
    lines = [b64[i:i + 70] for i in range(0, len(b64), 70)]
    return (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        + "\n".join(lines)
        + "\n-----END OPENSSH PRIVATE KEY-----\n"
    )


def _openssh_public_key(private: ed25519.Ed25519PrivateKey, comment: str) -> str:
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    blob = _ssh_string(b"ssh-ed25519") + _ssh_string(public)
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} {comment}\n"


def generate(out: Path, comment: str, force: bool) -> tuple[Path, Path]:
    pub_path = Path(str(out) + ".pub")
    if not force and (out.exists() or pub_path.exists()):
        raise SystemExit(f"уже существует: {out} или {pub_path} (--force чтобы перезаписать)")

    private = ed25519.Ed25519PrivateKey.generate()
    out.parent.mkdir(parents=True, exist_ok=True)

    out.write_text(_openssh_private_key(private, comment), encoding="utf-8")
    os.chmod(out, 0o600)
    pub_path.write_text(_openssh_public_key(private, comment), encoding="utf-8")
    os.chmod(pub_path, 0o644)
    return out, pub_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Генерация ed25519 SSH-ключа без ssh-keygen")
    parser.add_argument("--out", default=str(Path.home() / ".ssh" / "id_ed25519"),
                        help="путь к приватному ключу")
    parser.add_argument("--comment", default="",
                        help="комментарий (обычно email или логин)")
    parser.add_argument("--force", action="store_true", help="перезаписать существующий ключ")
    args = parser.parse_args()

    comment = args.comment or f"{os.uname().nodename}"
    priv, pub = generate(Path(args.out).expanduser(), comment, args.force)
    print(f"приватный: {priv}")
    print(f"публичный: {pub}")
    print()
    print("--- добавь этот ключ в GitHub: Settings -> SSH and GPG keys -> New SSH key ---")
    print(pub.read_text(encoding="utf-8").strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
