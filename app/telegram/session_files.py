"""Inspect session files on disk without connecting anywhere.
Telethon and Pyrogram both store sessions as SQLite databases; we detect the format from the schema."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

TELETHON_TABLES = {"sessions", "entities", "version"}
PYROGRAM_TABLES = {"sessions", "peers", "version"}


@dataclass
class SessionFileInfo:
    path: Path
    exists: bool
    size: int = 0
    fmt: str = "unknown"          # telethon | pyrogram | unknown | not_sqlite
    dc_id: int | None = None
    user_id: int | None = None
    is_bot: bool | None = None
    tables: list[str] = field(default_factory=list)
    error: str | None = None


def inspect_session_file(path: Path) -> SessionFileInfo:
    info = SessionFileInfo(path=path, exists=path.exists())
    if not info.exists:
        info.error = "file missing"
        return info
    info.size = path.stat().st_size
    if info.size < 16:
        info.fmt = "not_sqlite"
        info.error = "file too small"
        return info
    with open(path, "rb") as f:
        header = f.read(16)
    if not header.startswith(b"SQLite format 3"):
        info.fmt = "not_sqlite"
        info.error = "not an SQLite session database"
        return info
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cur = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            info.tables = sorted(r[0] for r in cur.fetchall())
            names = set(info.tables)
            if PYROGRAM_TABLES <= names and "peers" in names:
                info.fmt = "pyrogram"
                row = con.execute("SELECT dc_id, user_id, is_bot FROM sessions LIMIT 1").fetchone()
                if row:
                    info.dc_id, info.user_id, info.is_bot = row[0], row[1], bool(row[2]) if row[2] is not None else None
            elif TELETHON_TABLES <= names or "entities" in names:
                info.fmt = "telethon"
                try:
                    row = con.execute("SELECT dc_id FROM sessions LIMIT 1").fetchone()
                    if row:
                        info.dc_id = row[0]
                except sqlite3.Error:
                    pass
            else:
                info.fmt = "unknown"
                info.error = "SQLite file without a recognised session schema"
        finally:
            con.close()
    except sqlite3.Error as exc:
        info.fmt = "unknown"
        info.error = f"sqlite error: {exc}"
    return info


def create_synthetic_session_file(path: Path, fmt: str = "telethon", dc_id: int = 2, user_id: int | None = None) -> Path:
    """Used by tests and benchmarks only: creates a structurally valid session DB with NO real credentials."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    try:
        if fmt == "pyrogram":
            con.executescript(
                "CREATE TABLE sessions(dc_id INTEGER, api_id INTEGER, test_mode INTEGER, auth_key BLOB, date INTEGER,"
                " user_id INTEGER, is_bot INTEGER);"
                "CREATE TABLE peers(id INTEGER PRIMARY KEY, access_hash INTEGER, type TEXT, phone_number TEXT,"
                " last_update_on INTEGER);"
                "CREATE TABLE version(number INTEGER);"
            )
            con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)", (dc_id, 0, 0, b"\x00" * 256, 0, user_id, 0))
            con.execute("INSERT INTO version VALUES (3)")
        else:
            con.executescript(
                "CREATE TABLE version(version INTEGER PRIMARY KEY);"
                "CREATE TABLE sessions(dc_id INTEGER PRIMARY KEY, server_address TEXT, port INTEGER, auth_key BLOB,"
                " takeout_id INTEGER);"
                "CREATE TABLE entities(id INTEGER PRIMARY KEY, hash INTEGER NOT NULL, username TEXT, phone INTEGER,"
                " name TEXT, date INTEGER);"
                "CREATE TABLE sent_files(md5_digest BLOB, file_size INTEGER, type INTEGER, id INTEGER, hash INTEGER,"
                " PRIMARY KEY(md5_digest, file_size, type));"
                "CREATE TABLE update_state(id INTEGER PRIMARY KEY, pts INTEGER, qts INTEGER, date INTEGER, seq INTEGER);"
            )
            con.execute("INSERT INTO version VALUES (7)")
            con.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", (dc_id, "149.154.167.51", 443, b"\x00" * 256, None))
        con.commit()
    finally:
        con.close()
    return path
