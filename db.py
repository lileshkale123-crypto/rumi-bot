import logging
import os
from pathlib import Path

import asyncpg

log = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


class _NoResetConn(asyncpg.Connection):
    def get_reset_query(self) -> str:
        return ""


async def init_pool() -> asyncpg.Pool:
    """Create the asyncpg connection pool and apply schema.sql once at startup."""
    global _pool
    if _pool is not None and not _pool._closed:
        return _pool

    dsn = os.environ["DATABASE_URL"]
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=4,
        max_size=10,
        connection_class=_NoResetConn,
        command_timeout=30,
        max_inactive_connection_lifetime=300.0,
    )

    schema_path = Path(__file__).resolve().with_name("schema.sql")
    schema_sql = schema_path.read_text(encoding="utf-8")

    async with _pool.acquire() as conn:
        await conn.execute(schema_sql)

    return _pool


async def close_pool() -> None:
    """Close the connection pool cleanly during shutdown."""
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception as exc:
            log.warning("Error closing database pool: %s", exc)
        finally:
            _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None or _pool._closed:
        raise RuntimeError("Database pool not initialized — call init_pool() first")
    return _pool
