from __future__ import annotations

import csv
import hashlib
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .answering import estimate_tokens, evidence_identifier, truncate_to_token_budget

_ALLOWED_CONFIDENTIALITY = {"personal", "public"}
_SKIPPED_DIRS = {".obsidian", ".trash", "templates", "template", "attachments"}
_MAX_NOTE_BYTES = 8 * 1024 * 1024
_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
_QUERY_STOPWORDS = {"a", "an", "and", "are", "as", "at", "be", "between", "can", "do", "does", "for", "from", "how", "in", "is", "it", "of", "on", "or", "the", "to", "what", "which", "who", "why", "with", "yet"}
_EMBED_RE = re.compile(r"!\[\[[^\]]*\]\]|!\[[^\]]*\]\([^)]*\)")
_FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


def eligible_note(metadata: dict[str, Any]) -> bool:
    """Apply the explicit, deny-by-default retrieval policy."""
    return metadata.get("ai_index") is True and metadata.get("confidentiality") in _ALLOWED_CONFIDENTIALITY


def remote_eligible_note(metadata: dict[str, Any]) -> bool:
    """Remote sharing is a separate explicit permission, not implied by ai_index."""
    return eligible_note(metadata) and metadata.get("ai_remote_share") is True


def _strip_frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    try:
        end = next(i for i in range(1, min(len(lines), 1000)) if lines[i].strip() == "---")
    except StopIteration:
        return ""
    return "\n".join(lines[end + 1:])


def secure_data_dir(path: str | Path) -> None:
    """Restrict a dedicated plugin-data directory to its owner and Windows admins/system."""
    directory = Path(path).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        icacls = system_root / "icacls.exe"
        whoami = system_root / "whoami.exe"
        if not icacls.is_file() or not whoami.is_file():
            raise PermissionError("Windows ACL hardening tools are unavailable")
        identity = subprocess.run(
            [str(whoami), "/user", "/fo", "csv", "/nh"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        try:
            account = next(csv.reader([identity]))
            user_sid = account[-1]
        except (StopIteration, IndexError, csv.Error) as exc:
            raise PermissionError("Could not resolve the current Windows account SID") from exc
        if not user_sid.startswith("S-"):
            raise PermissionError("Could not resolve the current Windows account SID")
        commands = (
            [icacls, str(directory), "/reset", "/T", "/C"],
            [icacls, str(directory), "/inheritance:r"],
            [
                icacls, str(directory), "/grant:r",
                f"*{user_sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F",
                "/T", "/C",
            ],
        )
        for command in commands:
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
            output = result.stdout + "\n" + result.stderr
            failed = re.search(r"Failed processing\s+(\d+)\s+files?", output, re.IGNORECASE)
            if result.returncode != 0 or (failed and int(failed.group(1)) > 0):
                raise PermissionError("Could not apply the Windows ACL to the local index directory")
        return
    os.chmod(directory, 0o700)
    if directory.stat().st_mode & 0o777 != 0o700:
        raise PermissionError("Could not restrict the local index directory permissions")


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse only simple scalar frontmatter fields; unsupported YAML fails closed."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(i for i in range(1, min(len(lines), 1000)) if lines[i].strip() == "---")
    except StopIteration:
        return {}
    values: dict[str, Any] = {}
    for line in lines[1:end]:
        match = _FRONTMATTER_KEY_RE.match(line)
        if not match:
            continue
        key, raw = match.groups()
        raw = raw.split(" #", 1)[0].strip()
        if raw in {"true", "false"}:
            value: Any = raw == "true"
        elif len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
            value = raw[1:-1]
        else:
            value = raw
        values[key] = value
    return values


def _clean_text(text: str) -> str:
    text = _EMBED_RE.sub(" ", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    return text


def _chunks(text: str) -> tuple[str, list[tuple[str, str]]]:
    title = ""
    sections: list[tuple[str, list[str]]] = []
    current_heading = "Document"
    current_lines: list[str] = []
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            if current_lines:
                sections.append((current_heading, current_lines))
            current_heading = match.group(2).strip()
            if not title and len(match.group(1)) == 1:
                title = current_heading
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_heading, current_lines))
    output: list[tuple[str, str]] = []
    max_chars = 2400
    for heading, lines in sections:
        body = _clean_text("\n".join(lines)).strip()
        if not body:
            continue
        paragraphs = re.split(r"\n\s*\n", body)
        part = ""
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if part and len(part) + len(paragraph) + 2 > max_chars:
                output.append((heading, part))
                part = ""
            while len(paragraph) > max_chars:
                output.append((heading, paragraph[:max_chars]))
                paragraph = paragraph[max_chars:]
            part = f"{part}\n\n{paragraph}".strip()
        if part:
            output.append((heading, part))
    return title, output


def _is_candidate(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if path.is_symlink() or path.name.startswith(".") or path.suffix.casefold() != ".md":
        return False
    if path.stem.casefold().startswith("template"):
        return False
    if any(part.startswith(".") or part.casefold() in _SKIPPED_DIRS for part in relative.parts[:-1]):
        return False
    return True


def _excerpt(text: str, query: str, max_chars: int = 520) -> str:
    tokens = [token.casefold() for token in _TOKEN_RE.findall(query)]
    folded = text.casefold()
    positions = [folded.find(token) for token in tokens if token and folded.find(token) >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - max_chars // 3)
    end = min(len(text), start + max_chars)
    if end - start < max_chars:
        start = max(0, end - max_chars)
    excerpt = text[start:end].strip()
    return ("…" if start else "") + excerpt + ("…" if end < len(text) else "")


class LocalIndex:
    """Local SQLite FTS5 index containing only currently eligible Markdown notes."""

    def __init__(self, vault_root: str | Path, database_path: str | Path):
        self.root = Path(vault_root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Vault path must be a directory")
        self.db_path = Path(database_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.db_path), timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS notes (
                path TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL,
                modified TEXT NOT NULL,
                knowledge_status TEXT
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL,
                title TEXT NOT NULL,
                heading TEXT NOT NULL,
                body TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS chunks_by_path ON chunks(path);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                title, heading, body, content='chunks', content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, title, heading, body)
                VALUES (new.id, new.title, new.heading, new.body);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, title, heading, body)
                VALUES ('delete', old.id, old.title, old.heading, old.body);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, title, heading, body)
                VALUES ('delete', old.id, old.title, old.heading, old.body);
                INSERT INTO chunks_fts(rowid, title, heading, body)
                VALUES (new.id, new.title, new.heading, new.body);
            END;
            """
        )
        self.db.execute("INSERT INTO chunks_fts(chunks_fts, rank) VALUES('secure-delete', 1)")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "LocalIndex":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def indexed_paths(self) -> list[str]:
        return [row[0] for row in self.db.execute("SELECT path FROM notes ORDER BY path")]

    def rebuild_or_update(self) -> dict[str, int]:
        eligible: dict[str, tuple[Path, bytes, dict[str, Any], int]] = {}
        for path in self.root.rglob("*.md"):
            if not _is_candidate(self.root, path):
                continue
            relative = path.relative_to(self.root).as_posix()
            try:
                stat = path.stat()
                if stat.st_size > _MAX_NOTE_BYTES:
                    continue
                raw = path.read_bytes()
                text = raw.decode("utf-8-sig", errors="replace")
                metadata = parse_frontmatter(text)
                if eligible_note(metadata):
                    eligible[relative] = (path, raw, metadata, stat.st_mtime_ns)
            except (OSError, ValueError):
                continue

        existing = {row["path"]: row for row in self.db.execute("SELECT * FROM notes")}
        changed = removed = 0
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for relative, row in existing.items():
                if relative not in eligible:
                    self.db.execute("DELETE FROM chunks WHERE path = ?", (relative,))
                    self.db.execute("DELETE FROM notes WHERE path = ?", (relative,))
                    removed += 1
            for relative, (path, raw, metadata, mtime_ns) in eligible.items():
                digest = hashlib.sha256(raw).hexdigest()
                previous = existing.get(relative)
                if previous and previous["sha256"] == digest:
                    if previous["mtime_ns"] != mtime_ns:
                        self.db.execute(
                            "UPDATE notes SET mtime_ns=?, modified=? WHERE path=?",
                            (mtime_ns, _mtime(path), relative),
                        )
                    continue
                text = _strip_frontmatter(raw.decode("utf-8-sig", errors="replace"))
                title, pieces = _chunks(text)
                self.db.execute("DELETE FROM chunks WHERE path = ?", (relative,))
                self.db.execute(
                    "INSERT INTO notes(path,sha256,mtime_ns,modified,knowledge_status) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256,mtime_ns=excluded.mtime_ns,"
                    "modified=excluded.modified,knowledge_status=excluded.knowledge_status",
                    (relative, digest, mtime_ns, _mtime(path), str(metadata.get("knowledge_status", "")) or None),
                )
                self.db.executemany(
                    "INSERT INTO chunks(path,title,heading,body) VALUES(?,?,?,?)",
                    [(relative, title, heading, body) for heading, body in pieces],
                )
                changed += 1
            self.db.commit()
            if removed:
                self._secure_purge()
        except Exception:
            self.db.rollback()
            raise
        return {"indexed": len(eligible), "updated": changed, "removed": removed}

    def _secure_purge(self) -> None:
        """Scrub deleted passages from SQLite pages and WAL before continuing."""
        for _ in range(2):
            checkpoint = self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint and checkpoint[0]:
                raise sqlite3.OperationalError("index cleanup is busy; refusing retrieval")
            if _ == 0:
                self.db.execute("VACUUM")

    def _query_rows(self, query: str, limit: int, knowledge_status: str | None, folder_prefix: str | None) -> list[sqlite3.Row]:
        tokens = [token for token in _TOKEN_RE.findall(query) if token.casefold() not in _QUERY_STOPWORDS]
        if not tokens:
            tokens = _TOKEN_RE.findall(query)
        if not tokens:
            return []
        match = " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens[:20])
        where = ["chunks_fts MATCH ?"]
        args: list[Any] = [match]
        if knowledge_status:
            where.append("notes.knowledge_status = ?")
            args.append(knowledge_status)
        if folder_prefix:
            prefix = folder_prefix.replace("\\", "/").strip("/")
            if ".." in Path(prefix).parts:
                return []
            where.append("(chunks.path = ? OR chunks.path LIKE ? ESCAPE '\\')")
            escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args.extend([prefix, escaped + "/%"])
        args.append(limit)
        sql = (
            "SELECT chunks.path,chunks.title,chunks.heading,chunks.body,notes.modified,notes.knowledge_status,"
            "bm25(chunks_fts,8.0,4.0,1.0) AS rank "
            "FROM chunks_fts JOIN chunks ON chunks.id=chunks_fts.rowid JOIN notes ON notes.path=chunks.path "
            "WHERE " + " AND ".join(where) + " ORDER BY rank LIMIT ?"
        )
        try:
            rows = list(self.db.execute(sql, args))
        except sqlite3.OperationalError:
            rows = []
        if rows or len(tokens) == 1:
            return rows
        args[0] = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens[:20])
        try:
            return list(self.db.execute(sql, args))
        except sqlite3.OperationalError:
            # Malformed or unsupported search input fails closed.
            return []

    def search(
        self, query: str, limit: int = 5, knowledge_status: str | None = None,
        folder_prefix: str | None = None, *, remote_only: bool = False,
    ) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        limit = max(1, min(int(limit), 10))
        for attempt in range(2):
            self.rebuild_or_update()
            rows = self._query_rows(query, limit * 8, knowledge_status, folder_prefix)
            if not rows:
                return []
            results: list[dict[str, Any]] = []
            seen_paths: set[str] = set()
            stale = False
            for row in rows:
                relative = row["path"]
                if relative in seen_paths:
                    continue
                seen_paths.add(relative)
                source_path = self.root / relative
                if source_path.is_symlink():
                    stale = True
                    break
                path = source_path.resolve()
                try:
                    path.relative_to(self.root)
                    if path.suffix.casefold() != ".md" or path.name.startswith("."):
                        stale = True
                        break
                    raw = path.read_bytes()
                    metadata = parse_frontmatter(raw.decode("utf-8-sig", errors="replace"))
                    if not eligible_note(metadata) or hashlib.sha256(raw).hexdigest() != self.db.execute(
                        "SELECT sha256 FROM notes WHERE path=?", (relative,)
                    ).fetchone()[0]:
                        stale = True
                        break
                except (OSError, ValueError, TypeError):
                    stale = True
                    break
                if remote_only and not remote_eligible_note(metadata):
                    continue
                source_hash = hashlib.sha256(raw).hexdigest()
                results.append({
                    "file_path": str(path),
                    "vault_relative_path": relative,
                    "heading": row["heading"],
                    "evidence_id": evidence_identifier(relative, row["heading"], source_hash),
                    "excerpt": _excerpt(row["body"], query),
                    "modified": row["modified"],
                    "knowledge_status": row["knowledge_status"],
                    "score": round(-float(row["rank"]), 8),
                })
                if len(results) >= limit:
                    break
            if not stale:
                return results
            if attempt == 1:
                return []
        return []

    def expand_section(
        self, candidate: dict[str, Any], *, token_budget: int, remote_only: bool = False,
    ) -> dict[str, Any]:
        """Read a full matching section after rechecking path, hash and privacy metadata."""
        try:
            budget = max(1, min(int(token_budget), 100_000))
        except (TypeError, ValueError):
            raise ValueError("token_budget must be a positive integer")
        relative = str(candidate.get("vault_relative_path", ""))
        if not relative or ".." in Path(relative).parts:
            raise PermissionError("Candidate path is invalid")
        source_path = self.root / relative
        if not _is_candidate(self.root, source_path) or source_path.is_symlink():
            raise PermissionError("Candidate path is not eligible")
        try:
            path = source_path.resolve(strict=True)
            path.relative_to(self.root)
            raw = path.read_bytes()
            text = raw.decode("utf-8-sig", errors="replace")
            metadata = parse_frontmatter(text)
        except (OSError, ValueError) as exc:
            raise PermissionError("Candidate source cannot be read safely") from exc
        if not eligible_note(metadata) or (remote_only and not remote_eligible_note(metadata)):
            raise PermissionError("Candidate source is not approved for this retrieval mode")
        digest = hashlib.sha256(raw).hexdigest()
        stored = self.db.execute("SELECT sha256 FROM notes WHERE path=?", (relative,)).fetchone()
        if stored is None or stored[0] != digest:
            self.rebuild_or_update()
            raise RuntimeError("Candidate changed; refresh retrieval before expansion")
        heading = str(candidate.get("heading", ""))
        _, pieces = _chunks(_strip_frontmatter(text))
        section_parts = [body for piece_heading, body in pieces if piece_heading == heading]
        if not section_parts:
            raise RuntimeError("Candidate section is no longer available")
        section_text = "\n\n".join(section_parts)
        expanded_text = truncate_to_token_budget(section_text, budget)
        return {
            "evidence_id": evidence_identifier(relative, heading, digest),
            "file_path": str(path),
            "vault_relative_path": relative,
            "path": relative,
            "heading": heading,
            "text": expanded_text,
            "estimated_tokens": estimate_tokens(expanded_text),
            "truncated": len(expanded_text) < len(section_text),
            "modified": _mtime(path),
            "knowledge_status": str(metadata.get("knowledge_status", "")) or None,
        }

    def direct_search(
        self, query: str, limit: int = 3, *, remote_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Fallback title/heading/full-text scan for weak or empty FTS results."""
        terms = {term.casefold() for term in _TOKEN_RE.findall(query) if term.casefold() not in _QUERY_STOPWORDS}
        if not terms:
            terms = {term.casefold() for term in _TOKEN_RE.findall(query)}
        if not terms:
            return []
        self.rebuild_or_update()
        rows = self.db.execute(
            "SELECT chunks.path,chunks.title,chunks.heading,chunks.body,notes.modified,notes.knowledge_status,notes.sha256 "
            "FROM chunks JOIN notes ON notes.path=chunks.path ORDER BY chunks.path,chunks.id"
        )
        ranked: dict[tuple[str, str], tuple[int, sqlite3.Row]] = {}
        metadata_cache: dict[str, dict[str, Any] | None] = {}
        for row in rows:
            relative = row["path"]
            if relative not in metadata_cache:
                try:
                    path = self.root / relative
                    if path.is_symlink():
                        metadata_cache[relative] = None
                    else:
                        resolved = path.resolve(strict=True)
                        resolved.relative_to(self.root)
                        raw = resolved.read_bytes()
                        current = parse_frontmatter(raw.decode("utf-8-sig", errors="replace"))
                        valid = eligible_note(current) and hashlib.sha256(raw).hexdigest() == row["sha256"]
                        metadata_cache[relative] = current if valid else None
                except (OSError, ValueError):
                    metadata_cache[relative] = None
            metadata = metadata_cache[relative]
            if metadata is None or (remote_only and not remote_eligible_note(metadata)):
                continue
            title_terms = {term.casefold() for term in _TOKEN_RE.findall(row["title"])}
            heading_terms = {term.casefold() for term in _TOKEN_RE.findall(row["heading"])}
            body_terms = {term.casefold() for term in _TOKEN_RE.findall(row["body"])}
            score = 4 * len(terms & title_terms) + 3 * len(terms & heading_terms) + len(terms & body_terms)
            if score:
                key = (relative, row["heading"])
                if key not in ranked or score > ranked[key][0]:
                    ranked[key] = (score, row)
        ordered = sorted(ranked.values(), key=lambda item: (-item[0], item[1]["path"], item[1]["heading"]))
        output: list[dict[str, Any]] = []
        for score, row in ordered[:max(1, min(int(limit), 10))]:
            relative = row["path"]
            path = self.root / relative
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            output.append({
                "file_path": str(path.resolve()),
                "vault_relative_path": relative,
                "heading": row["heading"],
                "evidence_id": evidence_identifier(relative, row["heading"], digest),
                "excerpt": _excerpt(row["body"], query),
                "modified": row["modified"],
                "knowledge_status": row["knowledge_status"],
                "score": float(score),
            })
        return output

    def read_note(
        self, candidate: dict[str, Any], *, token_budget: int, remote_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Direct-note baseline: read each section of one selected note under one shared budget."""
        try:
            remaining = max(0, min(int(token_budget), 100_000))
        except (TypeError, ValueError):
            raise ValueError("token_budget must be a non-negative integer")
        relative = str(candidate.get("vault_relative_path", ""))
        if not relative or ".." in Path(relative).parts:
            raise PermissionError("Candidate path is invalid")
        rows = self.db.execute(
            "SELECT heading FROM chunks WHERE path=? GROUP BY heading ORDER BY MIN(id)", (relative,),
        )
        output: list[dict[str, Any]] = []
        for row in rows:
            if remaining <= 0:
                break
            section_candidate = dict(candidate, heading=row["heading"])
            try:
                item = self.expand_section(section_candidate, token_budget=remaining, remote_only=remote_only)
            except (PermissionError, RuntimeError, OSError, ValueError):
                continue
            if item["text"]:
                output.append(item)
                remaining -= item["estimated_tokens"]
        return output


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
