"""Offline-only foundation; no model calls or network fetching."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA = """
CREATE TABLE IF NOT EXISTS imports (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    origin TEXT NOT NULL,
    kind TEXT NOT NULL,
    analysis_basis TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    UNIQUE(content_hash, kind)
);
"""


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def import_text(db: Path, source: Path, title: str, kind: str) -> dict:
    if kind not in {"text", "transcript"}:
        raise ValueError("kind must be text or transcript")
    if not source.is_file():
        raise ValueError("input must be a regular UTF-8 text file")
    if source.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("input exceeds 2 MiB")
    content = source.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError("input is empty")
    if not title.strip():
        raise ValueError("title must not be empty")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    ident = hashlib.sha256((kind + ":" + digest).encode()).hexdigest()[:24]
    basis = "transcript" if kind == "transcript" else "user_supplied"
    conn = connect_db(db)
    try:
        with conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ident, title.strip(), content, digest, source.name, kind, basis,
                 datetime.now(timezone.utc).isoformat()),
            )
            created = cursor.rowcount == 1
    finally:
        conn.close()
    return {"id": ident, "created": created, "analysis_basis": basis,
            "status": "collected", "generation_status": "not_implemented"}


def doctor() -> dict:
    try:
        datetime.now(ZoneInfo("Asia/Jerusalem"))
        tz_status = "available"
    except ZoneInfoNotFoundError:
        tz_status = "missing_tzdata"
    return {"python": platform.python_version(), "os": platform.system(),
            "git": bool(shutil.which("git")), "ffmpeg": bool(shutil.which("ffmpeg")),
            "timezone": "Asia/Jerusalem", "timezone_status": tz_status,
            "strict_free": True, "model_mode": "manual",
            "network_fetch": "not_implemented", "generation": "not_implemented",
            "note": "No credentials are read and no model or network calls are made."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Moh AI offline project foundation")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("MOH_AI_DATA_DIR", "data")))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Report local tools without network or credentials")
    sub.add_parser("init-db", help="Create the local imports database")
    ingest = sub.add_parser("ingest", help="Import a local user-supplied text/transcript")
    ingest.add_argument("--file", required=True, type=Path)
    ingest.add_argument("--title", required=True)
    ingest.add_argument("--kind", choices=["text", "transcript"], default="text")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor()
        elif args.command == "init-db":
            conn = connect_db(args.data_dir / "moh-ai.sqlite3")
            conn.close()
            result = {"status": "initialized", "scope": "imports_foundation_only"}
        else:
            result = import_text(args.data_dir / "moh-ai.sqlite3", args.file,
                                 args.title, args.kind)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
