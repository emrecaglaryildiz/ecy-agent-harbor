"""Local encrypted credential storage. Back up the master key with the database."""
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet

LOCK = threading.Lock()


def cipher(database_path, create=False):
    key_path = Path(database_path).parent / "master.key"
    with LOCK:
        if not key_path.exists():
            if not create:
                raise RuntimeError("Şifreleme anahtarı eksik; data/master.key yedeğini geri yükle")
            key_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as file:
                file.write(Fernet.generate_key())
        return Fernet(key_path.read_bytes())


def encrypt(database_path, value):
    return cipher(database_path, create=True).encrypt(value.encode()).decode() if value else ""


def decrypt(database_path, value):
    return cipher(database_path).decrypt(value.encode()).decode() if value else ""
