from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from research_automation.models import CausalClaim

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_document_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT NOT NULL,
    company_id TEXT,
    filing_type TEXT,
    period_end TEXT,
    content_hash TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_document_id)
);

CREATE TABLE IF NOT EXISTS causal_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    cause_variable TEXT NOT NULL,
    effect_variable TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('increase', 'decrease', 'mixed', 'unknown')),
    transmission_channel TEXT NOT NULL,
    horizon TEXT NOT NULL CHECK(horizon IN ('immediate', 'short', 'medium', 'long', 'unknown')),
    modality TEXT NOT NULL CHECK(modality IN ('observed', 'forecast', 'possible', 'conditional', 'uncertain')),
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    evidence_text TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'reported_claim'
        CHECK(verification_status IN ('reported_claim', 'data_checked', 'analyst_reviewed', 'rejected')),
    extractor_model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(document_id, cause_variable, effect_variable, evidence_text, prompt_version)
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    variable_id TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    value REAL,
    unit TEXT,
    source TEXT NOT NULL,
    vintage_date TEXT,
    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(variable_id, observation_date, source, vintage_date)
);

CREATE TABLE IF NOT EXISTS market_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    currency TEXT,
    source TEXT NOT NULL,
    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(security_id, observed_at, source)
);

CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    operation TEXT NOT NULL,
    document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    prompt_version TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_published_at ON documents(published_at);
CREATE INDEX IF NOT EXISTS idx_documents_company_id ON documents(company_id);
CREATE INDEX IF NOT EXISTS idx_claims_document_id ON causal_claims(document_id);
CREATE INDEX IF NOT EXISTS idx_claims_cause_effect ON causal_claims(cause_variable, effect_variable);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(path: Path) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)


def upsert_document(path: Path, document: Dict[str, Any]) -> int:
    with connect(path) as connection:
        connection.execute(
            """
            INSERT INTO documents (
                source, source_document_id, url, title, published_at,
                company_id, filing_type, period_end, content_hash, raw_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_document_id) DO UPDATE SET
                url = excluded.url,
                title = excluded.title,
                published_at = excluded.published_at,
                company_id = excluded.company_id,
                filing_type = excluded.filing_type,
                period_end = excluded.period_end,
                content_hash = excluded.content_hash,
                raw_text = excluded.raw_text,
                collected_at = CURRENT_TIMESTAMP
            """,
            (
                document["source"],
                document["source_document_id"],
                document["url"],
                document["title"],
                document["published_at"],
                document.get("company_id"),
                document.get("filing_type"),
                document.get("period_end"),
                document["content_hash"],
                document["raw_text"],
            ),
        )
        row = connection.execute(
            "SELECT id FROM documents WHERE source = ? AND source_document_id = ?",
            (document["source"], document["source_document_id"]),
        ).fetchone()
        if row is None:
            raise RuntimeError("document upsert succeeded but row could not be found")
        return int(row["id"])


def get_document(path: Path, document_id: int) -> Optional[sqlite3.Row]:
    with connect(path) as connection:
        return connection.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()


def insert_claims(
    path: Path,
    document_id: int,
    claims: Iterable[CausalClaim],
    model: str,
    prompt_version: str,
) -> int:
    inserted = 0
    with connect(path) as connection:
        for claim in claims:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO causal_claims (
                    document_id, cause_variable, effect_variable, direction,
                    transmission_channel, horizon, modality, confidence,
                    evidence_text, extractor_model, prompt_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    claim.cause_variable,
                    claim.effect_variable,
                    claim.direction,
                    claim.transmission_channel,
                    claim.horizon,
                    claim.modality,
                    claim.confidence,
                    claim.evidence_text,
                    model,
                    prompt_version,
                ),
            )
            inserted += cursor.rowcount
    return inserted


def log_llm_usage(
    path: Path,
    provider: str,
    model: str,
    operation: str,
    document_id: int,
    prompt_version: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
) -> None:
    with connect(path) as connection:
        connection.execute(
            """
            INSERT INTO llm_usage (
                provider, model, operation, document_id, prompt_version,
                input_tokens, output_tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                model,
                operation,
                document_id,
                prompt_version,
                input_tokens,
                output_tokens,
            ),
        )


def upsert_market_prices(path: Path, prices: Iterable[Dict[str, Any]]) -> int:
    """같은 종목·시각·출처는 최신 값으로 덮어씁니다 (증권사가 과거 시세를 고쳐 내려줄 수 있음)."""
    written = 0
    with connect(path) as connection:
        for price in prices:
            connection.execute(
                """
                INSERT INTO market_prices (
                    security_id, observed_at, open, high, low, close, volume, currency, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(security_id, observed_at, source) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    currency = excluded.currency,
                    collected_at = CURRENT_TIMESTAMP
                """,
                (
                    price["security_id"],
                    price["observed_at"],
                    price.get("open"),
                    price.get("high"),
                    price.get("low"),
                    price.get("close"),
                    price.get("volume"),
                    price.get("currency"),
                    price["source"],
                ),
            )
            written += 1
    return written


def upsert_observations(path: Path, observations: Iterable[Dict[str, Any]]) -> int:
    written = 0
    with connect(path) as connection:
        for item in observations:
            # SQLite UNIQUE 는 NULL 끼리 같다고 보지 않으므로 vintage_date 가 없을 때는 먼저 지웁니다.
            if item.get("vintage_date") is None:
                connection.execute(
                    """
                    DELETE FROM observations
                    WHERE variable_id = ? AND observation_date = ? AND source = ?
                        AND vintage_date IS NULL
                    """,
                    (item["variable_id"], item["observation_date"], item["source"]),
                )
            connection.execute(
                """
                INSERT INTO observations (
                    variable_id, observation_date, value, unit, source, vintage_date
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(variable_id, observation_date, source, vintage_date) DO UPDATE SET
                    value = excluded.value,
                    unit = excluded.unit,
                    collected_at = CURRENT_TIMESTAMP
                """,
                (
                    item["variable_id"],
                    item["observation_date"],
                    item.get("value"),
                    item.get("unit"),
                    item["source"],
                    item.get("vintage_date"),
                ),
            )
            written += 1
    return written
