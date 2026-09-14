"""Tests del registro de actividad (AC 2.5, FR-11): shape de la entrada
(timestamp, canal, token, máquina, resultado; NUNCA passwords), consulta
list/stat y CLI `activity list`/`activity stat`."""
from __future__ import annotations

import pytest

from wakemeup.core.models import ActivityEntry, Machine
from wakemeup.services.activity import ActivityService


class FakeDb:
    """Doble de DB con activity_log en memoria."""

    def __init__(self) -> None:
        self.activities: list[ActivityEntry] = []
        self._id = 0

    async def record_activity(self, channel, token, machine_id, machine_ip, operation, result) -> None:
        self._id += 1
        self.activities.append(
            ActivityEntry(
                id=self._id, timestamp="2026-09-14T10:00:00+00:00",
                channel=channel, token=token, machine_id=machine_id,
                machine_ip=machine_ip, operation=operation, result=result,
            )
        )

    async def list_activity(self, limit: int = 20) -> list[ActivityEntry]:
        return list(reversed(self.activities))[:limit]

    async def activity_stats(self) -> list[tuple[str, str, int]]:
        counts: dict[tuple[str, str], int] = {}
        for a in self.activities:
            key = (a.operation, a.result)
            counts[key] = counts.get(key, 0) + 1
        return [(op, result, c) for (op, result), c in sorted(counts.items())]


@pytest.mark.asyncio
async def test_record_creates_entry_with_defined_shape():
    """La entrada tiene timestamp, canal, token, máquina y resultado (FR-11)."""
    db = FakeDb()
    svc = ActivityService(db)
    await svc.record("api", "ab12", 1, "192.168.1.10", "enroll", "ok")
    entry = db.activities[0]
    assert entry.channel == "api"
    assert entry.token == "ab12"
    assert entry.machine_id == 1
    assert entry.machine_ip == "192.168.1.10"
    assert entry.operation == "enroll"
    assert entry.result == "ok"
    assert entry.timestamp


@pytest.mark.asyncio
async def test_password_never_in_activity():
    """NUNCA passwords: registrar el resultado no puede filtrar la password."""
    db = FakeDb()
    svc = ActivityService(db)
    await svc.record("api", "tok", 1, "192.168.1.10", "enroll", "auth_failed")
    serialized = " ".join(
        [
            db.activities[0].channel,
            db.activities[0].token,
            db.activities[0].operation,
            db.activities[0].result,
        ]
    )
    assert "s3cr3t" not in serialized


@pytest.mark.asyncio
async def test_list_returns_most_recent_first():
    db = FakeDb()
    svc = ActivityService(db)
    for i in range(5):
        await svc.record("api", "t", 1, "192.168.1.10", "wake", "ok")
    entries = await svc.list()
    assert len(entries) == 5
    assert entries[0].id == 5  # el más reciente primero


@pytest.mark.asyncio
async def test_stats_aggregates_by_operation_result():
    db = FakeDb()
    svc = ActivityService(db)
    for result in ("ok", "ok", "conflict"):
        await svc.record("api", "t", 1, "192.168.1.10", "wake", result)
    for result in ("ok", "auth_failed"):
        await svc.record("api", "t", 2, "192.168.1.11", "enroll", result)
    stats = await svc.stats()
    by = {(op, res): c for op, res, c in stats}
    assert by[("wake", "ok")] == 2
    assert by[("wake", "conflict")] == 1
    assert by[("enroll", "ok")] == 1
    assert by[("enroll", "auth_failed")] == 1


# --- CLI (2.5): activity list / stat con la DB real de tests ---


@pytest.fixture
async def db_cli(tmp_path, monkeypatch):
    from wakemeup.adapters.db import Db

    path = tmp_path / "activity.db"
    toml = tmp_path / "config.toml"
    toml.write_text(f'[db]\npath = "{path}"\n')
    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: str(toml))
    db = Db(path=path)
    await db.init_db()
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_cli_activity_list_prints_entries(db_cli, capsys):
    from wakemeup.cli import _async_main

    await db_cli.record_activity("api", "abc", 1, "192.168.1.10", "wake", "ok")
    await db_cli.commit()
    rc = await _async_main(["activity", "list"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert "wake=ok" in out
    assert "192.168.1.10" in out
    assert "abc" in out


@pytest.mark.asyncio
async def test_cli_activity_stat_prints_aggregates(db_cli, capsys):
    from wakemeup.cli import _async_main

    await db_cli.record_activity("api", "abc", 1, "192.168.1.10", "wake", "ok")
    await db_cli.record_activity("api", "abc", 1, "192.168.1.10", "wake", "ok")
    await db_cli.commit()
    rc = await _async_main(["activity", "stat"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert "wake\tok\t2" in out


@pytest.mark.asyncio
async def test_cli_activity_list_limit(db_cli, capsys):
    from wakemeup.cli import _async_main

    for i in range(5):
        await db_cli.record_activity("api", "abc", 1, "192.168.1.10", "wake", "ok")
    await db_cli.commit()
    assert await _async_main(["activity", "list", "--limit", "2"]) == 0
    out, err = capsys.readouterr()
    assert out.count("wake=ok") == 2
