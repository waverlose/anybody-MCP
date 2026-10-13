from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import closing
from dataclasses import dataclass, fields
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = PROJECT_ROOT / "workspace" / ".mcp_knowledge"
REMOTE_DOCS_PATH = KNOWLEDGE_DIR / "remote_documents.jsonl"
INDEX_DB_PATH = KNOWLEDGE_DIR / "knowledge.sqlite"

USER_AGENT = "anybody-mcp-knowledge/1.0 (+local research index)"
DEFAULT_TIMEOUT = 20

logger = logging.getLogger(__name__)

DEFAULT_PUBMED_IDS = [
    "34198674",
    "33554113",
    "31825728",
    "16095407",
]

DEFAULT_PUBMED_QUERIES = [
    "bench press grip width electromyography",
    "bench press grip width biomechanics",
    "bench press muscle activation pectoralis triceps deltoid",
    "bench press shoulder biomechanics resistance training",
    "bench press kinematics kinetics",
    "resistance exercise upper limb electromyography biomechanics",
]

DEFAULT_REMOTE_SEEDS = [
    {
        "source_type": "official_doc",
        "title": "AnyBody Documentation",
        "url": "https://www.anybodytech.com/resources/documentation/",
        "max_depth": 1,
    },
    {
        "source_type": "official_doc",
        "title": "AMMR Documentation",
        "url": "https://anyscript.org/ammr/",
        "max_depth": 1,
    },
    {
        "source_type": "official_doc",
        "title": "AnyScript Getting Started",
        "url": "https://anyscript.org/tutorials/A_Getting_started_anyscript/intro.html",
        "max_depth": 1,
    },
    {
        "source_type": "official_doc",
        "title": "AnyBody Interface Features",
        "url": "https://anyscript.org/tutorials/Interface_features/intro.html",
        "max_depth": 1,
    },
    {
        "source_type": "pmc_fulltext",
        "title": "Bench press grip width PMC article",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10458411/",
        "max_depth": 0,
    },
]

DEFAULT_LOCAL_PATTERNS = [
    "README.md",
    "DELIVERY.md",
    "AGENTS.md",
    "docs/*.md",
    "src/*.py",
    "scripts/*.py",
    "template/*.json",
    "workspace/**/*.md",
    "workspace/**/*.json",
    "workspace/**/*.ris",
    "workspace/**/README.txt",
]

EXCLUDED_LOCAL_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".mcp_knowledge",
    "output",
}


@dataclass
class KnowledgeDocument:
    document_id: str
    source_type: str
    title: str
    text: str
    path: str | None = None
    url: str | None = None
    doi: str | None = None
    year: str | None = None
    license: str | None = None
    retrieved_at: str | None = None
    metadata: dict | None = None

    def to_json(self) -> dict:
        return {
            "document_id": self.document_id,
            "source_type": self.source_type,
            "title": self.title,
            "text": self.text,
            "path": self.path,
            "url": self.url,
            "doi": self.doi,
            "year": self.year,
            "license": self.license,
            "retrieved_at": self.retrieved_at,
            "metadata": self.metadata or {},
        }


# Stored documents are read back by keyword, so a crawl written by an older
# revision with different fields does not break the loader.
_DOCUMENT_FIELDS = {field.name for field in fields(KnowledgeDocument)}


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_stack: list[str] = []
        self._in_title = False
        self.text_parts: list[str] = []
        self.title_parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_stack.append(tag)
        if tag == "title":
            self._in_title = True
        if tag == "a":
            for name, value in attrs:
                if name.lower() == "href" and value:
                    self.links.append(value)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_stack and self._skip_stack[-1] == tag:
            self._skip_stack.pop()
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_stack:
            return
        cleaned = data.strip()
        if not cleaned:
            return
        if self._in_title:
            self.title_parts.append(cleaned)
        self.text_parts.append(cleaned)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash_id(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()[:24]


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _request_text(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _is_allowed_url(url: str, seed_host: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc != seed_host:
        return False
    if any(part in parsed.path.lower() for part in ["/_sources/", "/genindex", "/search"]):
        return False
    return True


def _extract_html_document(url: str, source_type: str, fallback_title: str, html: str) -> tuple[KnowledgeDocument, list[str]]:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    title = _normalize_text(" ".join(parser.title_parts)) or fallback_title or url
    text = _normalize_text("\n".join(parser.text_parts))
    doc = KnowledgeDocument(
        document_id=_hash_id(url, text[:512]),
        source_type=source_type,
        title=title,
        text=text,
        url=url,
        license="public_web",
        retrieved_at=_now_iso(),
        metadata={"fetch_method": "html"},
    )
    links = [urllib.parse.urldefrag(urllib.parse.urljoin(url, link))[0] for link in parser.links]
    return doc, links


def _crawl_seed(seed: dict, max_pages: int, timeout: int) -> list[KnowledgeDocument]:
    root_url = seed["url"]
    seed_host = urllib.parse.urlparse(root_url).netloc
    limit = max(1, min(max_pages, int(seed.get("page_limit") or max_pages)))
    max_depth = int(seed.get("max_depth", 0))
    queue: list[tuple[str, int]] = [(root_url, 0)]
    seen: set[str] = set()
    docs: list[KnowledgeDocument] = []

    while queue and len(docs) < limit:
        url, depth = queue.pop(0)
        if url in seen or not _is_allowed_url(url, seed_host):
            continue
        seen.add(url)
        try:
            html = _request_text(url, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            docs.append(KnowledgeDocument(
                document_id=_hash_id(url, str(exc)),
                source_type="crawl_error",
                title=f"Fetch failed: {url}",
                text=str(exc),
                url=url,
                retrieved_at=_now_iso(),
                metadata={"seed": root_url},
            ))
            continue

        doc, links = _extract_html_document(url, seed["source_type"], seed.get("title", ""), html)
        if len(doc.text) >= 200:
            docs.append(doc)
        if depth < max_depth:
            for link in links:
                if link not in seen and _is_allowed_url(link, seed_host):
                    queue.append((link, depth + 1))
    return docs


def _parse_pubmed_article(article: ET.Element) -> KnowledgeDocument | None:
    pmid = article.findtext(".//PMID")
    if not pmid:
        return None
    title = "".join(article.find(".//ArticleTitle").itertext()) if article.find(".//ArticleTitle") is not None else f"PubMed {pmid}"
    abstract_parts = ["".join(node.itertext()) for node in article.findall(".//AbstractText")]
    abstract = "\n".join(part.strip() for part in abstract_parts if part.strip())
    journal = article.findtext(".//Journal/Title") or ""
    year = (
        article.findtext(".//JournalIssue/PubDate/Year")
        or article.findtext(".//ArticleDate/Year")
        or ""
    )
    doi = None
    for node in article.findall(".//ArticleId"):
        if node.attrib.get("IdType") == "doi" and node.text:
            doi = node.text
            break
    text = _normalize_text("\n\n".join(part for part in [title, journal, abstract] if part))
    return KnowledgeDocument(
        document_id=f"pubmed-{pmid}",
        source_type="pubmed",
        title=_normalize_text(title),
        text=text,
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        doi=doi,
        year=year,
        license="metadata_abstract",
        retrieved_at=_now_iso(),
        metadata={"pmid": pmid, "journal": journal},
    )


def _fetch_pubmed(ids: Iterable[str], timeout: int) -> list[KnowledgeDocument]:
    id_list = [str(pmid).strip() for pmid in ids if str(pmid).strip()]
    if not id_list:
        return []
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        f"?db=pubmed&id={','.join(id_list)}&retmode=xml"
    )
    xml_text = _request_text(url, timeout=timeout)
    root = ET.fromstring(xml_text)
    docs = []
    for article in root.findall(".//PubmedArticle"):
        doc = _parse_pubmed_article(article)
        if doc and doc.text:
            docs.append(doc)
    return docs


def _search_pubmed(query: str, max_results: int, timeout: int) -> list[str]:
    params = urllib.parse.urlencode({
        "db": "pubmed",
        "term": query,
        "retmode": "xml",
        "retmax": max(1, min(max_results, 200)),
        "sort": "relevance",
    })
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{params}"
    xml_text = _request_text(url, timeout=timeout)
    root = ET.fromstring(xml_text)
    return [
        node.text.strip()
        for node in root.findall(".//Id")
        if node.text and node.text.strip()
    ]


def _merge_remote_documents(new_docs: list[KnowledgeDocument]) -> dict:
    existing = _load_remote_documents()
    merged: dict[str, KnowledgeDocument] = {}
    for doc in existing + new_docs:
        # PMID/URL based document_id keeps repeated crawls cumulative but deduplicated.
        merged[doc.document_id] = doc

    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    with REMOTE_DOCS_PATH.open("w", encoding="utf-8") as handle:
        for doc_id in sorted(merged):
            handle.write(json.dumps(merged[doc_id].to_json(), ensure_ascii=False) + "\n")

    return {
        "previous_documents": len(existing),
        "new_documents": len(new_docs),
        "total_documents": len(merged),
        "deduplicated": len(existing) + len(new_docs) - len(merged),
        "saved_to": str(REMOTE_DOCS_PATH),
    }


def crawl_professional_sources(
    source_set: str = "default",
    max_pages: int = 20,
    max_pubmed_results: int = 80,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Fetch whitelisted professional sources into normalized JSONL."""
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    selected = source_set.strip().lower()
    include_remote = selected in {"default", "all", "anybody", "official"}
    include_pubmed = selected in {"default", "all", "pubmed", "literature"}
    include_pmc = selected in {"default", "all", "pmc", "literature"}

    docs: list[KnowledgeDocument] = []
    errors: list[dict] = []
    if include_remote or include_pmc:
        for seed in DEFAULT_REMOTE_SEEDS:
            if seed["source_type"] == "pmc_fulltext" and not include_pmc:
                continue
            if seed["source_type"] == "official_doc" and not include_remote:
                continue
            try:
                docs.extend(_crawl_seed(seed, max_pages=max_pages, timeout=timeout))
            except Exception as exc:
                errors.append({"source": seed["url"], "error": str(exc)})

    if include_pubmed:
        try:
            pmids = set(DEFAULT_PUBMED_IDS)
            per_query = max(1, max_pubmed_results // max(1, len(DEFAULT_PUBMED_QUERIES)))
            query_hits: dict[str, int] = {}
            for query in DEFAULT_PUBMED_QUERIES:
                found = _search_pubmed(query=query, max_results=per_query, timeout=timeout)
                query_hits[query] = len(found)
                pmids.update(found)
            docs.extend(_fetch_pubmed(sorted(pmids), timeout=timeout))
            for doc in docs:
                if doc.source_type == "pubmed":
                    doc.metadata = {
                        **(doc.metadata or {}),
                        "query_hits": query_hits,
                    }
        except Exception as exc:
            errors.append({"source": "pubmed", "error": str(exc)})

    merge_result = _merge_remote_documents(docs)

    return {
        "source_set": source_set,
        "documents_fetched": len(docs),
        "documents_total": merge_result["total_documents"],
        "deduplicated": merge_result["deduplicated"],
        "errors": errors,
        "saved_to": merge_result["saved_to"],
    }


def _load_remote_documents() -> list[KnowledgeDocument]:
    if not REMOTE_DOCS_PATH.exists():
        return []
    docs = []
    with REMOTE_DOCS_PATH.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                docs.append(KnowledgeDocument(**{k: v for k, v in data.items() if k in _DOCUMENT_FIELDS}))
            except (json.JSONDecodeError, TypeError, AttributeError) as exc:
                # One damaged line must not make the whole stored corpus unreadable.
                logger.warning("Skipping unreadable document at line %d: %s", line_number, exc)
    return docs


def _is_excluded(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return any(part.lower() in parts for part in EXCLUDED_LOCAL_PARTS)


def _iter_local_files(paths: list[str] | None = None) -> list[Path]:
    if paths:
        candidates: list[Path] = []
        for item in paths:
            path = Path(item)
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            if path.is_dir():
                candidates.extend(path.rglob("*"))
            elif path.exists():
                candidates.append(path)
        return sorted({path for path in candidates if path.is_file() and not _is_excluded(path)})

    files: set[Path] = set()
    for pattern in DEFAULT_LOCAL_PATTERNS:
        files.update(PROJECT_ROOT.glob(pattern))
    return sorted(path for path in files if path.is_file() and not _is_excluded(path))


def _read_local_document(path: Path) -> KnowledgeDocument | None:
    if path.suffix.lower() not in {".md", ".txt", ".json", ".ris", ".py"}:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    text = _normalize_text(text)
    if len(text) < 40:
        return None
    try:
        relative = path.relative_to(PROJECT_ROOT)
    except ValueError:
        relative = path
    return KnowledgeDocument(
        document_id=_hash_id(str(relative), text[:512]),
        source_type="local_project",
        title=relative.as_posix(),
        text=text,
        path=str(path),
        license="local",
        retrieved_at=_now_iso(),
        metadata={"suffix": path.suffix.lower()},
    )


def _collect_local_documents(paths: list[str] | None = None) -> list[KnowledgeDocument]:
    docs: list[KnowledgeDocument] = []
    for path in _iter_local_files(paths):
        doc = _read_local_document(path)
        if doc:
            docs.append(doc)
    return docs


def _chunk_document(doc: KnowledgeDocument, max_chars: int = 1800, overlap: int = 220) -> list[dict]:
    text = doc.text
    chunks: list[dict] = []
    start = 0
    index = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            paragraph_break = text.rfind("\n\n", start + 400, end)
            if paragraph_break > start:
                end = paragraph_break
        chunk_text = text[start:end].strip()
        if chunk_text:
            line_start = text.count("\n", 0, start) + 1 if doc.path else None
            chunks.append({
                "chunk_id": f"{doc.document_id}-{index}",
                "document_id": doc.document_id,
                "chunk_index": index,
                "text": chunk_text,
                "char_start": start,
                "char_end": end,
                "line_start": line_start,
            })
            index += 1
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _connect() -> sqlite3.Connection:
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(INDEX_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def rebuild_knowledge_index(paths: list[str] | None = None, include_remote: bool = True) -> dict:
    """Build a local SQLite FTS index from local and crawled documents."""
    docs = _collect_local_documents(paths)
    if include_remote:
        docs.extend(_load_remote_documents())
    unique_docs: list[KnowledgeDocument] = []
    seen_doc_ids: set[str] = set()
    for doc in docs:
        if doc.document_id in seen_doc_ids:
            continue
        seen_doc_ids.add(doc.document_id)
        unique_docs.append(doc)
    docs = unique_docs

    # closing() releases the handle; the connection context manager commits the work.
    with closing(_connect()) as conn, conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS documents;
            DROP TABLE IF EXISTS chunks;
            DROP TABLE IF EXISTS chunks_fts;

            CREATE TABLE documents (
                document_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                title TEXT NOT NULL,
                path TEXT,
                url TEXT,
                doi TEXT,
                year TEXT,
                license TEXT,
                retrieved_at TEXT,
                metadata_json TEXT,
                text TEXT NOT NULL
            );

            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                title TEXT NOT NULL,
                path TEXT,
                url TEXT,
                doi TEXT,
                year TEXT,
                license TEXT,
                chunk_index INTEGER NOT NULL,
                line_start INTEGER,
                body TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                chunk_id UNINDEXED,
                document_id UNINDEXED,
                source_type UNINDEXED,
                title,
                body,
                path UNINDEXED,
                url UNINDEXED,
                tokenize='unicode61'
            );
            """
        )

        chunk_count = 0
        for doc in docs:
            conn.execute(
                """
                INSERT INTO documents (
                    document_id, source_type, title, path, url, doi, year,
                    license, retrieved_at, metadata_json, text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc.document_id,
                    doc.source_type,
                    doc.title,
                    doc.path,
                    doc.url,
                    doc.doi,
                    doc.year,
                    doc.license,
                    doc.retrieved_at,
                    json.dumps(doc.metadata or {}, ensure_ascii=False),
                    doc.text,
                ),
            )
            for chunk in _chunk_document(doc):
                conn.execute(
                    """
                    INSERT INTO chunks (
                        chunk_id, document_id, source_type, title, path, url,
                        doi, year, license, chunk_index, line_start, body
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk["chunk_id"],
                        doc.document_id,
                        doc.source_type,
                        doc.title,
                        doc.path,
                        doc.url,
                        doc.doi,
                        doc.year,
                        doc.license,
                        chunk["chunk_index"],
                        chunk["line_start"],
                        chunk["text"],
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO chunks_fts (
                        chunk_id, document_id, source_type, title, body, path, url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk["chunk_id"],
                        doc.document_id,
                        doc.source_type,
                        doc.title,
                        chunk["text"],
                        doc.path,
                        doc.url,
                    ),
                )
                chunk_count += 1

    return {
        "indexed_documents": len(docs),
        "indexed_chunks": chunk_count,
        "database": str(INDEX_DB_PATH),
        "include_remote": include_remote,
    }


def _fts_query(query: str) -> str:
    terms = re.findall(r"[\w\u4e00-\u9fff]+", query, flags=re.UNICODE)
    # MCP queries are often natural-language questions; OR avoids over-filtering long questions.
    return " OR ".join(f'"{term}"' for term in terms[:20])


def search_knowledge(query: str, top_k: int = 5, source_type: str | None = None) -> dict:
    if not INDEX_DB_PATH.exists():
        return {
            "error": "Knowledge index not found. Run rebuild_knowledge_index first.",
            "database": str(INDEX_DB_PATH),
        }
    fts = _fts_query(query)
    if not fts:
        return {"query": query, "matches": []}
    limit = max(1, min(int(top_k), 20))

    sql = """
        SELECT
            c.chunk_id,
            c.document_id,
            c.source_type,
            c.title,
            c.path,
            c.url,
            c.doi,
            c.year,
            c.license,
            c.line_start,
            c.body,
            bm25(chunks_fts) AS score
        FROM chunks_fts
        JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
        WHERE chunks_fts MATCH ?
    """
    params: list[object] = [fts]
    if source_type:
        sql += " AND c.source_type = ?"
        params.append(source_type)
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)

    # closing() releases the handle; the connection context manager commits the work.
    with closing(_connect()) as conn, conn:
        rows = conn.execute(sql, params).fetchall()

    matches = []
    for row in rows:
        text = row["body"]
        matches.append({
            "score": row["score"],
            "source_type": row["source_type"],
            "title": row["title"],
            "path": row["path"],
            "url": row["url"],
            "doi": row["doi"],
            "year": row["year"],
            "license": row["license"],
            "line_start": row["line_start"],
            "text": text[:1200],
        })
    return {
        "query": query,
        "fts_query": fts,
        "source_type": source_type,
        "matches": matches,
    }


def knowledge_stats() -> dict:
    remote_count = len(_load_remote_documents()) if REMOTE_DOCS_PATH.exists() else 0
    if not INDEX_DB_PATH.exists():
        return {
            "database": str(INDEX_DB_PATH),
            "exists": False,
            "remote_documents_path": str(REMOTE_DOCS_PATH),
            "remote_documents_exists": REMOTE_DOCS_PATH.exists(),
            "remote_documents": remote_count,
        }
    # closing() releases the handle; the connection context manager commits the work.
    with closing(_connect()) as conn, conn:
        doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        by_source = [
            dict(row)
            for row in conn.execute(
                "SELECT source_type, COUNT(*) AS documents FROM documents GROUP BY source_type ORDER BY source_type"
            )
        ]
    return {
        "database": str(INDEX_DB_PATH),
        "exists": True,
        "documents": doc_count,
        "chunks": chunk_count,
        "by_source": by_source,
        "remote_documents_path": str(REMOTE_DOCS_PATH),
        "remote_documents_exists": REMOTE_DOCS_PATH.exists(),
        "remote_documents": remote_count,
    }


def knowledge_source_catalog() -> dict:
    return {
        "remote_seeds": DEFAULT_REMOTE_SEEDS,
        "pubmed_ids": DEFAULT_PUBMED_IDS,
        "pubmed_queries": DEFAULT_PUBMED_QUERIES,
        "local_patterns": DEFAULT_LOCAL_PATTERNS,
        "excluded_local_parts": sorted(EXCLUDED_LOCAL_PARTS),
        "storage_dir": str(KNOWLEDGE_DIR),
    }
