"""Tests de la CLI de tokens (spec 1.4): alta muestra token UNA sola vez,
solo el hash en la BD, revocación por nombre y listado (FR-10, AD-6).
"""
from __future__ import annotations

import asyncio
import re

import pytest

from wakemeup.adapters.db import Db
from wakemeup.config import Settings
from wakemeup.core.models import Token
from wakemeup.services.auth import sha256_hex_lower

from wakemeup.cli import _async_main


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """DB de test + config [db] apuntando a ella (la CLI usa el mismo fichero)."""
    path = tmp_path / "cli.db"
    toml = tmp_path / "config.toml"
    toml.write_text(f'[db]\npath = "{path}"\n')
    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: str(toml))
    db = Db(path=path)
    await db.init_db()
    yield db
    await db.close()


def _capture_stdout(capsys):
    out, err = capsys.readouterr()
    return out, err


async def test_create_shows_token_once_and_stores_only_hash(db, capsys):
    rc = await _async_main(["token", "create", "movil-roberto"])
    out, err = _capture_stdout(capsys)
    assert rc == 0
    lines = [l for l in out.splitlines() if l and not l.startswith("token de")]
    token = lines[-1]
    assert re.fullmatch(r"[0-9a-f]{64}", token)
    tok = [t for t in await db.list_tokens() if t.device_name == "movil-roberto"]
    assert len(tok) == 1
    assert tok[0].token_sha256 == sha256_hex_lower(token)
    assert "token de" in err  # el aviso de "una sola vez" va a stderr


async def test_create_duplicate_name_fails(db, capsys):
    assert await _async_main(["token", "create", "dupe"]) == 0
    assert await _async_main(["token", "create", "dupe"]) == 1
    assert len([t for t in await db.list_tokens() if t.device_name == "dupe"]) == 1


async def test_revoke_by_name(db, capsys):
    token = "a" * 64
    await db.create_token("viejo", sha256_hex_lower(token))
    await db.commit()
    assert await _async_main(["token", "revoke", "viejo"]) == 0
    assert await db.token_exists(sha256_hex_lower(token)) is False
    assert await _async_main(["token", "revoke", "viejo"]) == 1  # ya revocado


async def test_revoke_by_token_value(db, capsys):
    """Revocar pasando el token plano (64 hex) → revoca por su hash (AD-6)."""
    token = "ff" * 32
    await db.create_token("por-valor", sha256_hex_lower(token))
    await db.commit()
    assert await _async_main(["token", "revoke", token]) == 0
    assert await db.token_exists(sha256_hex_lower(token)) is False
    # un 64-hex no registrado → error con pista
    assert await _async_main(["token", "revoke", "e" * 64]) == 1


async def test_revoke_unknown_name_fails(db, capsys):
    assert await _async_main(["token", "revoke", "no-existe"]) == 1


async def test_list_shows_active_and_revoked(db, capsys):
    token = "b" * 64
    await db.create_token("activo", sha256_hex_lower(token))
    await db.create_token("revocado", sha256_hex_lower("c" * 64))
    await db.commit()
    await db.revoke_token_by_name("revocado")
    await db.commit()
    await _async_main(["token", "list"])
    out, _ = _capture_stdout(capsys)
    lines = [l for l in out.splitlines() if l]
    by_name = {l.split("\t")[0]: l.split("\t")[1] for l in lines}
    assert by_name.get("activo") == "activo"
    assert by_name.get("revocado") == "revocado"


async def test_cli_uses_db_path_from_settings(tmp_path, monkeypatch, capsys):
    """[db] path del Settings (decisión 1.4): la DB de la CLI sigue la config."""
    toml = tmp_path / "config.toml"
    toml.write_text(f'[db]\npath = "{tmp_path / "cli2.db"}"\n')
    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: str(toml))
    assert await _async_main(["token", "create", "con-config"]) == 0
    assert (tmp_path / "cli2.db").exists()


async def test_init_db_migrates_legacy_1_2_db(tmp_path):
    """Migración 1.2→1.4 (verification-gap): una DB de la story 1.2 (sin
    columnas de estado ni tabla tokens) se evolve en init_db sin romper filas."""
    import sqlite3

    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.executescript(
        "CREATE TABLE machines ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "ip TEXT NOT NULL UNIQUE,"
        "mac TEXT,"
        "hostname TEXT,"
        "updated_at TEXT NOT NULL);"
    )
    conn.execute(
        "INSERT INTO machines (ip, mac, hostname, updated_at) VALUES (?, ?, ?, ?)",
        ("192.168.1.10", "aa:bb:cc:dd:ee:10", "pc-maria", "2026-09-13T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    db = Db(path=legacy)
    await db.init_db()

    rows = await db._conn.execute_fetchall("PRAGMA table_info(machines);")
    cols = {r[1] for r in rows}
    assert {"state", "status_checked_at"} <= cols

    tok_rows = await db._conn.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tokens';"
    )
    assert len(tok_rows) == 1

    machines = await db.list_machines()
    assert len(machines) == 1
    assert machines[0].ip == "192.168.1.10"
    assert machines[0].mac == "aa:bb:cc:dd:ee:10"
    assert machines[0].hostname == "pc-maria"
    assert machines[0].state == "offline"

    token = "d" * 64
    await db.create_token("post-migra", sha256_hex_lower(token))
    await db.commit()
    assert await db.token_exists(sha256_hex_lower(token)) is True

    await db.close()
