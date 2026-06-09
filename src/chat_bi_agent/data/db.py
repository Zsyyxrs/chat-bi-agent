"""Database connection and utilities."""

from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor


class DatabaseConfig:
    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        database: str = "chatbi",
        user: str = "chatbi",
        password: str = "chatbi_dev",
    ):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password

    def get_connection_string(self) -> str:
        return (
            f"dbname={self.database} user={self.user} password={self.password} "
            f"host={self.host} port={self.port}"
        )


@contextmanager
def get_connection(config: DatabaseConfig):
    """Context manager for database connections."""
    conn = psycopg2.connect(config.get_connection_string())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_cursor(config: DatabaseConfig, dict_cursor: bool = False):
    """Context manager for database cursors."""
    with get_connection(config) as conn:
        cursor_factory = RealDictCursor if dict_cursor else None
        cursor = conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cursor
        finally:
            cursor.close()


def truncate_tables(config: DatabaseConfig, tables: list[str]) -> int:
    """Truncate specified tables. Returns count of tables truncated."""
    with get_cursor(config) as cursor:
        for table in tables:
            cursor.execute(f"TRUNCATE TABLE {table} CASCADE")
        return len(tables)
