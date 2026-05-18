"""
Shared database connection and execution helpers for db_upload scripts.

Reads credentials from the .env file at the project root.
"""

from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data" / "processed"


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    with open(ROOT / ".env") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def connect() -> psycopg2.extensions.connection:
    e = _load_env()
    return psycopg2.connect(
        host=e["DATABASE_HOST"],
        port=int(e["DATABASE_PORT"]),
        dbname=e["DATABASE_DATABASE"],
        user=e["DATABASE_USER"],
        password=e["DATABASE_PASSWORD"],
    )


def execute(conn: psycopg2.extensions.connection, sql: str, params: Any = None) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
    conn.commit()


def execute_values(
    conn: psycopg2.extensions.connection,
    sql: str,
    data: list,
    template: str | None = None,
    page_size: int = 5_000,
) -> None:
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, data, template=template, page_size=page_size)
    conn.commit()


def fetch_all(conn: psycopg2.extensions.connection, sql: str, params: Any = None) -> list:
    with conn.cursor() as cur:
        try:
            cur.execute(sql, params)
            return cur.fetchall()
        except Exception as e:
            # THIS IS THE MAGIC LINE: 
            # If the query fails, rollback the transaction immediately!
            conn.rollback() 
            print(f"Query Failed: {e}")
            raise  # Re-raise the error so you know what actually went wrong
