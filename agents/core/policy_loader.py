"""
Policy loader — reads policy_terms.json into a typed Policy object.

Why a dedicated loader?
  1. Single point of failure if the JSON is malformed (we want loud errors)
  2. Path resolution: works the same in tests, API, and Streamlit
  3. Caches the loaded policy (it's read on every claim, but never changes)
  4. Type-safety: the loader maps JSON strings to our enums (e.g. "CONSULTATION" -> ClaimCategory.CONSULTATION)
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from agents.core.domain import (
    Exclusions,
    FraudThresholds,
    Member,
    OPDCategoryConfig,
    Policy,
    WaitingPeriods,
)
from agents.core.enums import ClaimCategory, DocumentType, Relationship


# Path to the policy file — works whether called from project root or anywhere else
_DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "data" / "policy_terms.json"


def _coerce_member(m: dict[str, Any]) -> Member:
    """Map a raw member dict to the Member model.

    Handles the JSON quirk where 'CHILDREN' is sometimes 'CHILD' (we accept both).
    """
    rel = m.get("relationship", "SELF")
    if rel == "CHILD":
        rel = "CHILDREN"
    if rel == "PARENT":
        rel = "PARENTS"
    return Member(
        member_id=m["member_id"],
        name=m["name"],
        date_of_birth=m["date_of_birth"],
        gender=m.get("gender"),
        relationship=Relationship(rel),
        join_date=m.get("join_date"),
        dependents=m.get("dependents", []),
        primary_member_id=m.get("primary_member_id"),
    )


def _coerce_category(name: str, cfg: dict[str, Any]) -> OPDCategoryConfig:
    """Map one OPD category config."""
    return OPDCategoryConfig(**cfg)


def _coerce_doc_requirements(raw: dict[str, Any]) -> dict[ClaimCategory, dict[str, list[DocumentType]]]:
    """Map document_requirements into typed enums. JSON keys are uppercase
    in document_requirements (verified against policy_terms.json)."""
    out: dict[ClaimCategory, dict[str, list[DocumentType]]] = {}
    for cat, rules in raw.items():
        try:
            cat_enum = ClaimCategory(cat.upper())
        except ValueError:
            continue
        out[cat_enum] = {
            "required": [DocumentType(d) for d in rules.get("required", [])],
            "optional": [DocumentType(d) for d in rules.get("optional", [])],
        }
    return out


def load_policy(path: Path | str | None = None) -> Policy:
    """Load and parse policy_terms.json into a typed Policy object.

    Args:
        path: Optional path override. Defaults to data/policy_terms.json
              relative to the project root.

    Returns:
        A fully-typed Policy object with enums, validated dates, etc.

    Raises:
        FileNotFoundError: if the policy file doesn't exist
        ValueError: if the JSON is malformed or required fields are missing
    """
    path = Path(path) if path else _DEFAULT_POLICY_PATH
    if not path.exists():
        raise FileNotFoundError(f"Policy file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    # Map opd_categories: keys in JSON are lowercase ("consultation"), our enums
    # are uppercase ("CONSULTATION"). Normalize to uppercase before enum lookup.
    opd_categories: dict[ClaimCategory, OPDCategoryConfig] = {}
    for cat_name, cfg in raw.get("opd_categories", {}).items():
        try:
            cat_enum = ClaimCategory(cat_name.upper())
        except ValueError:
            continue
        opd_categories[cat_enum] = _coerce_category(cat_name, cfg)

    return Policy(
        policy_id=raw["policy_id"],
        policy_name=raw["policy_name"],
        insurer=raw.get("insurer"),
        opd_categories=opd_categories,
        sum_insured_per_employee=raw.get("coverage", {}).get("sum_insured_per_employee", 0),
        annual_opd_limit=raw.get("coverage", {}).get("annual_opd_limit", 0),
        per_claim_limit=raw.get("coverage", {}).get("per_claim_limit", 0),
        waiting_periods=WaitingPeriods(**raw.get("waiting_periods", {})),
        exclusions=Exclusions(**raw.get("exclusions", {})),
        pre_authorization=raw.get("pre_authorization", {}),
        fraud_thresholds=FraudThresholds(**raw.get("fraud_thresholds", {})),
        document_requirements=_coerce_doc_requirements(raw.get("document_requirements", {})),
        network_hospitals=raw.get("network_hospitals", []),
        submission_deadline_days=raw.get("submission_rules", {}).get("deadline_days_from_treatment", 30),
        minimum_claim_amount=raw.get("submission_rules", {}).get("minimum_claim_amount", 500),
        currency=raw.get("submission_rules", {}).get("currency", "INR"),
        members=[_coerce_member(m) for m in raw.get("members", [])],
        raw=raw,
    )


@lru_cache(maxsize=1)
def get_policy() -> Policy:
    """Cached policy loader — the policy is read once per process.

    We use lru_cache so repeated calls (every claim reads policy) are free.
    """
    return load_policy()


def reset_policy_cache() -> None:
    """Clear the policy cache. Useful in tests."""
    get_policy.cache_clear()
