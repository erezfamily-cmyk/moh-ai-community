"""Local workbench CLI. No model API calls; HTTPS fetch is explicit."""
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
            "strict_free": True, "model_mode": "ollama_local_optional",
            "network_fetch": "explicit_public_https", "generation": "ollama_local_optional",
            "note": "Doctor performs no model/network calls; production runs only against loopback Ollama."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Moh AI local editorial workbench")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("MOH_AI_DATA_DIR", "data")))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Report local tools without network or credentials")
    sub.add_parser("init-db", help="Create the local imports database")
    ingest = sub.add_parser("ingest", help="Import a local user-supplied text/transcript")
    ingest.add_argument("--file", required=True, type=Path)
    ingest.add_argument("--title", required=True)
    ingest.add_argument("--kind", choices=["text", "transcript"], default="text")
    web = sub.add_parser("serve", help="Open loopback-only Hebrew editorial workbench")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--local-model", default=None, help="Installed Ollama model name, e.g. qwen3:8b")
    source = sub.add_parser("source", help="Parse local text, HTML, VTT or SRT into source record")
    source.add_argument("--file", type=Path, required=True)
    source.add_argument("--kind", choices=["text","article","html","transcript","vtt","srt","description"], default="text")
    source.add_argument("--title", default="")
    source.add_argument("--url")
    fetch = sub.add_parser("fetch", help="Explicitly fetch one public HTTPS text source")
    fetch.add_argument("--url", required=True)
    pack = sub.add_parser("import-pack", help="Import evidence pack JSON with traceable claims")
    pack.add_argument("--file", type=Path, required=True)
    brief = sub.add_parser("brief", help="Prepare a writing brief without generating a post")
    brief.add_argument("--source-id", required=True)
    draft = sub.add_parser("add-draft", help="Import a manually/session-assisted written draft")
    draft.add_argument("--source-id", required=True)
    draft.add_argument("--file", type=Path, required=True)
    draft.add_argument("--mappings", type=Path, required=True)
    review = sub.add_parser("review", help="Import an explicitly attributed review report")
    review.add_argument("--draft-id", required=True)
    review.add_argument("--file", type=Path, required=True)
    export = sub.add_parser("export", help="Export draft ZIP; final export needs UI approval")
    export.add_argument("--draft-id", required=True)
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--final", action="store_true")
    sub.add_parser("pilot", help="Load the real assisted pilot with reviews, never auto-approve")
    image = sub.add_parser("render-image", help="Render an explanatory PNG locally (optional Pillow+RAQM)")
    image.add_argument("--draft-id", required=True)
    image.add_argument("--output", required=True, type=Path)
    image.add_argument("--font")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor()
        elif args.command == "init-db":
            conn = connect_db(args.data_dir / "moh-ai.sqlite3")
            conn.close()
            result = {"status": "initialized", "scope": "imports_foundation_only"}
        elif args.command == "ingest":
            result = import_text(args.data_dir / "moh-ai.sqlite3", args.file,
                                 args.title, args.kind)
        else:
            from . import editorial
            db = args.data_dir / "moh-ai.sqlite3"
            if args.command == "serve":
                from .web import serve
                serve(db,args.port,local_model=args.local_model)
                return 0
            elif args.command == "source":
                from .sources import parse_source
                result = parse_source(args.file.read_text(encoding="utf-8"),args.kind,title=args.title,url=args.url)
            elif args.command == "fetch":
                from .sources import fetch_source
                result = fetch_source(args.url)
            elif args.command == "import-pack":
                result = editorial.save_pack(db,json.loads(args.file.read_text(encoding="utf-8")))
            elif args.command == "brief":
                result = editorial.make_brief(db,args.source_id)
            elif args.command == "add-draft":
                result = editorial.save_draft(db,args.source_id,args.file.read_text(encoding="utf-8"),json.loads(args.mappings.read_text(encoding="utf-8")))
            elif args.command == "review":
                result = editorial.record_review(db,args.draft_id,json.loads(args.file.read_text(encoding="utf-8")))
            elif args.command == "export":
                output = editorial.export_bundle(db,args.draft_id,args.final)
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_bytes(output)
                result = {"path":str(args.output),"status":"exported","final":args.final}
            elif args.command == "pilot":
                from .pilot import load_pilot
                result = load_pilot(db)
            elif args.command == "render-image":
                from .media import render_png
                d = editorial.get_draft(db,args.draft_id)
                pack = editorial.get_pack(db,d["source_id"])
                result = render_png(pack["source"]["title"],d["body"],args.output,args.font)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
