from pathlib import Path

from research_automation import db
from research_automation.models import CausalClaim


def sample_document() -> dict:
    return {
        "source": "sec_edgar",
        "source_document_id": "0000000000-26-000001",
        "url": "https://example.test/filing",
        "title": "Example 10-Q",
        "published_at": "2026-01-01",
        "company_id": "0000000000",
        "filing_type": "10-Q",
        "period_end": "2025-12-31",
        "content_hash": "abc123",
        "raw_text": "Higher input costs reduced operating margin.",
    }


def test_document_upsert_and_claim_deduplication(tmp_path: Path) -> None:
    database_path = tmp_path / "research.db"
    db.init_db(database_path)
    first_id = db.upsert_document(database_path, sample_document())
    second_id = db.upsert_document(database_path, sample_document())
    assert first_id == second_id

    claim = CausalClaim(
        cause_variable="input costs",
        effect_variable="operating margin",
        direction="decrease",
        transmission_channel="cost pressure",
        horizon="short",
        modality="observed",
        confidence=0.95,
        evidence_text="Higher input costs reduced operating margin.",
    )
    assert db.insert_claims(database_path, first_id, [claim], "test-model", "v1") == 1
    assert db.insert_claims(database_path, first_id, [claim], "test-model", "v1") == 0
