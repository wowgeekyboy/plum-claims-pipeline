"""
Regenerate docs/COMPONENT_CONTRACTS.md from per-agent README.md files.

Usage: python scripts/generate_contracts.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = [
    "document_verification",
    "document_extraction",
    "member_validation",
    "policy_rules",
    "fraud_detection",
    "decision",
]


def main() -> None:
    out: list[str] = []
    out.append("# Component Contracts — Plum Claims Pipeline")
    out.append("")
    out.append("> **Deliverable #3** — precise, machine-checkable contracts for every significant component.")
    out.append("> Each component's contract is precise enough that another engineer could reimplement it")
    out.append("> without reading its code.")
    out.append("")
    out.append("This document is auto-generated from the per-agent README.md files in")
    out.append("`agents/*/README.md`. To regenerate: `python scripts/generate_contracts.py`")
    out.append("")
    out.append("---")
    out.append("")
    out.append("## Index of Components")
    out.append("")
    out.append("| # | Component | Purpose | Reads | Writes |")
    out.append("|---|-----------|---------|-------|--------|")
    out.append("| 1 | DocumentVerification | Catch wrong/missing docs early | Claim input | `document_verification` |")
    out.append("| 2 | DocumentExtraction | Extract structured fields from docs | Verified documents | `extracted_documents` |")
    out.append("| 3 | MemberValidation | Member + waiting periods | Member, diagnosis | `member_validation` |")
    out.append("| 4 | PolicyRules | Sub-limits, co-pay, exclusions, pre-auth | Extracted bill + category | `policy_evaluation` |")
    out.append("| 5 | FraudDetection | Same-day/monthly limits, high-value | Claim history | `fraud_evaluation` |")
    out.append("| 6 | Decision | Synthesize all signals → final | All upstream | `decision` |")
    out.append("| 7 | Orchestrator | LangGraph state machine wiring | Claim | All of the above |")
    out.append("")
    out.append("---")
    out.append("")

    for i, agent in enumerate(AGENTS, 1):
        readme = ROOT / "agents" / agent / "README.md"
        if not readme.exists():
            print(f"  WARNING: {readme} not found, skipping")
            continue
        out.append(f"## {i}. {agent.replace('_', ' ').title()}")
        out.append("")
        out.append(readme.read_text(encoding="utf-8").rstrip())
        out.append("")
        out.append("---")
        out.append("")

    out.append("## Cross-Component Contracts")
    out.append("")
    out.append("### State Contract (`AgentState`)")
    out.append("")
    out.append("All agents read from and write to a shared `AgentState` (TypedDict).")
    out.append("See `agents/core/state.py` for the canonical definition.")
    out.append("")
    out.append("### Error Handling Contract")
    out.append("")
    out.append("- **No agent crashes the pipeline.** Every agent catches its own exceptions and returns a result with `confidence` reduced and `error`/`warnings` populated.")
    out.append("- **Failed agents are visible in the trace.** The orchestrator's trace records which agents failed.")
    out.append("- **Graceful degradation**: if an upstream agent fails, downstream agents operate on whatever data they have (or skip with a `SKIPPED` status).")
    out.append("")
    out.append("### Confidence Contract")
    out.append("")
    out.append("- Every agent emits a `confidence` value in [0, 1].")
    out.append("- The Decision agent computes the final `confidence_score` as:")
    out.append("  `final = max(0.5, mean(upstream_confidences) - 0.1 * num_failed_agents)`")
    out.append("- Failed agents contribute their last-known confidence (or 0.5 default), and incur a 0.1 penalty each.")
    out.append("")
    out.append("### Trace Contract")
    out.append("")
    out.append("Every agent emits exactly one `AgentTrace` (success/fail/skip). The trace contains:")
    out.append("- `agent_name`, `status`, `started_at`, `completed_at`, `duration_ms`")
    out.append("- `confidence_contribution` (its confidence)")
    out.append("- `input_summary` / `output_summary` (redacted — no PHI payloads)")
    out.append("- `notes` (free-form) and `error` (if failed)")
    out.append("")

    out_path = ROOT / "docs" / "COMPONENT_CONTRACTS.md"
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
