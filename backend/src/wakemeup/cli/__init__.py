"""CLI del BE: alta/revocación de tokens de dispositivo (FR-10, AD-6) y
consulta del registro de actividad (FR-11, story 2.5).

Los tokens se muestran UNA sola vez; en la BD solo queda el hash SHA-256 de
la representación hex-lower (la CLI hashea la misma representación que la API
y el CLI de revocación). Los logs de actividad nunca contienen passwords.
Uso:

    wakemeup-cli token create <nombre>
    wakemeup-cli token revoke <nombre|token-hex-64>
    wakemeup-cli token list
    wakemeup-cli activity list [--limit N]
    wakemeup-cli activity stat
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys

from wakemeup.adapters.db import Db
from wakemeup.config import Settings
from wakemeup.services.auth import new_device_token, sha256_hex_lower


def _build_db() -> Db:
    settings = Settings()
    db = Db(settings.db.path)
    return db


async def _async_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wakemeup-cli", description="CLI del BE de wakemeupManager")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("token", help="gestión de tokens de dispositivo")
    p_create_tokens = p_create.add_subparsers(dest="token_action", required=True)

    p_tok_create = p_create_tokens.add_parser("create", help="alta de token (se muestra UNA sola vez)")
    p_tok_create.add_argument("name", help="nombre del dispositivo (único)")

    p_tok_revoke = p_create_tokens.add_parser("revoke", help="revocar token (por nombre de dispositivo o por el token)")
    p_tok_revoke.add_argument("target", help="nombre del dispositivo o el token a revocar")

    p_create_tokens.add_parser("list", help="listar tokens registrados")

    p_activity = sub.add_parser("activity", help="registro de actividad (2.5)")
    p_act_list = p_activity.add_subparsers(dest="activity_action", required=True)

    p_list = p_act_list.add_parser("list", help="mostrar las últimas entradas de actividad")
    p_list.add_argument("--limit", type=int, default=20, help="nº de entradas (default 20)")

    p_act_list.add_parser("stat", help="resumen agregado por operación y resultado")

    args = parser.parse_args(argv)

    db = _build_db()
    await db.init_db()
    try:
        if args.command == "activity":
            try:
                if args.activity_action == "list":
                    for e in await db.list_activity(limit=args.limit):
                        maquina = e.machine_ip or e.machine_id or "-"
                        print(f"{e.timestamp}  {e.channel:4s}  token={e.token[:12]}  "
                              f"máquina={maquina}  {e.operation}={e.result}")
                elif args.activity_action == "stat":
                    for operation, result, count in await db.activity_stats():
                        print(f"{operation}\t{result}\t{count}")
            except Exception as exc:
                logging.getLogger("wakemeup.cli").error("fallo en la operación CLI: %r", exc)
                return 1
        elif args.command == "token":
            try:
                if args.token_action == "create":
                    token = new_device_token()
                    await db.create_token(args.name, sha256_hex_lower(token))
                    await db.commit()
                    print(token)
                    print(f"token de '{args.name}' creado; muéstralo UNA sola vez.", file=sys.stderr)
                elif args.token_action == "revoke":
                    # Intento 1: por nombre de dispositivo. Intento 2: el
                    # argumento es el propio token → se revoca por su hash
                    # (misma pre-imagen canónica, AD-6).
                    if re.fullmatch(r"[0-9a-f]{64}", args.target):
                        revoked = await db.revoke_token(sha256_hex_lower(args.target))
                    else:
                        revoked = await db.revoke_token_by_name(args.target)
                    await db.commit()
                    if revoked:
                        print(f"token revocado.")
                    else:
                        print(
                            f"no existe un token activo para '{args.target}'. "
                            "Para revocar por valor, pásalo exactamente (64 hex).",
                            file=sys.stderr,
                        )
                        return 1
                elif args.token_action == "list":
                    for t in await db.list_tokens():
                        estado = "revocado" if t.revoked_at is not None else "activo"
                        print(f"{t.device_name}\t{estado}")
            except Exception as exc:  # SQLite UNIQUE conflict, etc.
                logger = logging.getLogger("wakemeup.cli")
                logger.error("fallo en la operación CLI: %r", exc)
                return 1
        return 0
    finally:
        await db.close()


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    try:
        sys.exit(asyncio.run(_async_main()))
    except KeyboardInterrupt:
        sys.exit(130)
