"""Unit tests for the local knowledge index.

The index is a SQLite FTS5 database built from project files and crawled
sources. These tests exercise chunking, indexed-path filtering, tolerant
loading of the stored corpus, and a full rebuild/search round trip against a
temporary directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import knowledge_index as ki


@pytest.fixture
def isolated_index(tmp_path: Path, monkeypatch):
    """Point the module's storage at a temp dir so tests never touch the real index."""
    root = tmp_path / "project"
    root.mkdir()
    knowledge = root / "workspace" / ".mcp_knowledge"
    monkeypatch.setattr(ki, "PROJECT_ROOT", root)
    monkeypatch.setattr(ki, "KNOWLEDGE_DIR", knowledge)
    monkeypatch.setattr(ki, "REMOTE_DOCS_PATH", knowledge / "remote_documents.jsonl")
    monkeypatch.setattr(ki, "INDEX_DB_PATH", knowledge / "knowledge.sqlite")
    return root


def make_document(text: str, doc_id: str = "doc-1", path: str | None = "notes.md") -> ki.KnowledgeDocument:
    return ki.KnowledgeDocument(
        document_id=doc_id, source_type="local_project", title="Notes", text=text, path=path
    )


# ─────────────────────────────────────────────────────────────
# Text helpers
# ─────────────────────────────────────────────────────────────


def test_normalize_text_collapses_whitespace_and_blank_runs():
    assert ki._normalize_text("a  \t b\r\n\r\n\r\n\r\nc") == "a b\n\nc"


def test_hash_id_is_deterministic_and_input_sensitive():
    assert ki._hash_id("a", "b") == ki._hash_id("a", "b")
    assert ki._hash_id("a", "b") != ki._hash_id("ab", "")
    assert len(ki._hash_id("a")) == 24


def test_fts_query_or_joins_quoted_terms():
    assert ki._fts_query("bench press") == '"bench" OR "press"'


def test_fts_query_drops_punctuation_only_input():
    assert ki._fts_query("!!! ???") == ""


def test_fts_query_caps_term_count():
    query = " ".join(f"term{i}" for i in range(50))
    assert ki._fts_query(query).count(" OR ") == 19  # 20 terms


def test_fts_query_keeps_cjk_terms():
    # An unbroken CJK run is one token, which is what the unicode61 tokenizer indexes.
    assert ki._fts_query("生物力学模型") == '"生物力学模型"'


# ─────────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────────


def test_chunk_document_covers_the_whole_text():
    doc = make_document("x" * 100)
    chunks = ki._chunk_document(doc, max_chars=40, overlap=5)
    assert len(chunks) > 1
    assert chunks[0]["char_start"] == 0
    assert chunks[-1]["char_end"] == 100


def test_chunk_document_indexes_and_ids_are_sequential():
    chunks = ki._chunk_document(make_document("y" * 200), max_chars=50, overlap=10)
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    assert all(c["chunk_id"] == f"doc-1-{c['chunk_index']}" for c in chunks)


def test_chunk_document_records_line_start_only_with_a_path():
    with_path = ki._chunk_document(make_document("z" * 50), max_chars=100)[0]
    without_path = ki._chunk_document(make_document("z" * 50, path=None), max_chars=100)[0]
    assert with_path["line_start"] == 1
    assert without_path["line_start"] is None


def test_chunk_document_short_text_is_one_chunk():
    chunks = ki._chunk_document(make_document("short body"), max_chars=1000)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "short body"


# ─────────────────────────────────────────────────────────────
# Local file selection
# ─────────────────────────────────────────────────────────────


def test_is_excluded_matches_any_path_segment(tmp_path: Path):
    assert ki._is_excluded(tmp_path / ".venv" / "lib" / "x.py")
    assert ki._is_excluded(tmp_path / "a" / "__pycache__" / "x.pyc")
    assert not ki._is_excluded(tmp_path / "src" / "runner.py")


def test_read_local_document_rejects_short_and_unsupported_files(tmp_path: Path):
    short = tmp_path / "short.md"
    short.write_text("too short", encoding="utf-8")
    assert ki._read_local_document(short) is None

    binary = tmp_path / "image.png"
    binary.write_bytes(b"\x89PNG" * 100)
    assert ki._read_local_document(binary) is None


def test_read_local_document_builds_relative_title(isolated_index: Path):
    target = isolated_index / "notes.md"
    target.write_text("A" * 100, encoding="utf-8")
    doc = ki._read_local_document(target)
    assert doc is not None
    assert doc.title == "notes.md"
    assert doc.source_type == "local_project"


def test_iter_local_files_honours_explicit_paths(isolated_index: Path):
    included = isolated_index / "a.md"
    included.write_text("body", encoding="utf-8")
    (isolated_index / ".venv").mkdir()
    excluded = isolated_index / ".venv" / "b.md"
    excluded.write_text("body", encoding="utf-8")

    found = ki._iter_local_files([str(isolated_index)])
    assert included in found
    assert excluded not in found


# ─────────────────────────────────────────────────────────────
# Stored corpus loading
# ─────────────────────────────────────────────────────────────


def test_load_remote_documents_missing_file_is_empty(isolated_index: Path):
    assert ki._load_remote_documents() == []


def test_load_remote_documents_skips_damaged_lines(isolated_index: Path):
    ki.KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    good = make_document("valid body", doc_id="good").to_json()
    ki.REMOTE_DOCS_PATH.write_text(
        json.dumps(good) + "\n{not json at all\n\n" + json.dumps({"document_id": "bare"}) + "\n",
        encoding="utf-8",
    )
    docs = ki._load_remote_documents()
    # The valid record survives; a malformed line and a record with no text do not
    # make the whole stored corpus unreadable.
    assert [d.document_id for d in docs] == ["good"]


def test_load_remote_documents_ignores_unknown_future_fields(isolated_index: Path):
    ki.KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    payload = make_document("body text here", doc_id="d1").to_json()
    payload["field_from_a_newer_version"] = "ignored"
    ki.REMOTE_DOCS_PATH.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    assert [d.document_id for d in ki._load_remote_documents()] == ["d1"]


# ─────────────────────────────────────────────────────────────
# Rebuild / search round trip
# ─────────────────────────────────────────────────────────────


def test_stats_before_index_reports_not_exists(isolated_index: Path):
    stats = ki.knowledge_stats()
    assert stats["exists"] is False


def test_rebuild_then_search_finds_the_chunk(isolated_index: Path):
    source = isolated_index / "grip.md"
    source.write_text(
        "Bench press grip width changes pectoralis and triceps activation.\n" * 20,
        encoding="utf-8",
    )

    rebuild = ki.rebuild_knowledge_index(paths=[str(source)], include_remote=False)
    assert rebuild["indexed_documents"] == 1
    assert rebuild["indexed_chunks"] >= 1

    result = ki.search_knowledge("pectoralis activation", top_k=3)
    assert result["matches"], "the indexed chunk should be retrievable"
    assert result["matches"][0]["path"] == str(source)
    assert "pectoralis" in result["matches"][0]["text"]


def test_search_without_index_reports_a_clear_error(isolated_index: Path):
    result = ki.search_knowledge("anything")
    assert "error" in result
    assert "rebuild_knowledge_index" in result["error"]


def test_search_source_type_filter_excludes_other_sources(isolated_index: Path):
    source = isolated_index / "only.md"
    source.write_text("deltoid activation pattern\n" * 20, encoding="utf-8")
    ki.rebuild_knowledge_index(paths=[str(source)], include_remote=False)

    assert ki.search_knowledge("deltoid", source_type="local_project")["matches"]
    assert ki.search_knowledge("deltoid", source_type="pubmed")["matches"] == []


def test_rebuild_replaces_previous_index(isolated_index: Path):
    first = isolated_index / "first.md"
    first.write_text("alpha content\n" * 30, encoding="utf-8")
    ki.rebuild_knowledge_index(paths=[str(first)], include_remote=False)

    second = isolated_index / "second.md"
    second.write_text("beta content\n" * 30, encoding="utf-8")
    result = ki.rebuild_knowledge_index(paths=[str(second)], include_remote=False)

    assert result["indexed_documents"] == 1
    assert ki.search_knowledge("alpha")["matches"] == []
    assert ki.search_knowledge("beta")["matches"]


def test_index_connection_is_released_after_use(isolated_index: Path):
    source = isolated_index / "doc.md"
    source.write_text("gamma content\n" * 30, encoding="utf-8")

    ki.rebuild_knowledge_index(paths=[str(source)], include_remote=False)
    ki.search_knowledge("gamma")
    ki.knowledge_stats()

    # An unclosed connection keeps the database locked on Windows.
    ki.INDEX_DB_PATH.unlink()
    assert not ki.INDEX_DB_PATH.exists()
