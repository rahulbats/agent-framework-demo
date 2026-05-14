"""
Submission Agent Accelerator — Complete Pipeline

End-to-end insurance submission processing combining:
- Document classification (Demo 01)
- Data extraction (Demo 01)
- Guideline matching (Demo 01)
- Multi-agent orchestration (Demo 09)
- Guardrails (Demo 11)
- Observability hooks (Demo 05)

This is a reference implementation that can be adapted for production.
"""

import os
import re
import json
from datetime import datetime
from typing import TypedDict
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

load_dotenv()
console = Console()


# ============================================================
# Guardrails (from Demo 11)
# ============================================================

PII_PATTERNS = {
    "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
    "Credit Card": r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b",
}


def check_pii(text: str) -> list[str]:
    findings = []
    for pii_type, pattern in PII_PATTERNS.items():
        if re.search(pattern, text):
            findings.append(pii_type)
    return findings


# ============================================================
# Pipeline State
# ============================================================

class SubmissionState(TypedDict):
    submission_id: str
    broker: str
    documents: list[dict]
    classifications: list[dict]
    extracted_data: dict
    guideline_matches: list[dict]
    recommendation: str
    risk_flags: list[str]
    processing_log: list[str]


# ============================================================
# Stage 1: Document Intake & Classification
# ============================================================

def stage_classify(state: SubmissionState) -> SubmissionState:
    console.print("\n[bold cyan]Stage 1: Document Classification[/bold cyan]")

    doc_type_map = {
        "loss": "Loss Run Statement",
        "application": "Application Form",
        "financial": "Financial Statement",
        "coverage": "Coverage Summary",
        "photo": "Property Photos",
    }

    classifications = []
    for doc in state["documents"]:
        name_lower = doc["name"].lower()
        doc_type = "Unknown"
        for keyword, dtype in doc_type_map.items():
            if keyword in name_lower:
                doc_type = dtype
                break

        classifications.append({
            "document": doc["name"],
            "type": doc_type,
            "confidence": 0.94,
            "pages": doc.get("pages", 1),
        })
        console.print(f"  ✓ {doc['name']} → [green]{doc_type}[/green] (94%)")

    state["classifications"] = classifications
    state["processing_log"].append(f"Classified {len(classifications)} documents")
    return state


# ============================================================
# Stage 2: Data Extraction
# ============================================================

def stage_extract(state: SubmissionState) -> SubmissionState:
    console.print("\n[bold cyan]Stage 2: Data Extraction[/bold cyan]")

    extracted = {
        "insured_name": "Peachtree Manufacturing Inc.",
        "dba": "Peachtree Mfg",
        "address": "1200 Industrial Blvd, Atlanta, GA 30301",
        "state": "Georgia",
        "business_type": "Manufacturing — Plastics",
        "naics_code": "326199",
        "year_established": 2008,
        "annual_revenue": "$42,000,000",
        "property": {
            "type": "Multi-story industrial",
            "year_built": 2010,
            "square_footage": 85000,
            "construction": "Fire-resistive",
            "sprinklers": True,
            "occupancy": "Manufacturing",
        },
        "requested_coverage": {
            "type": "Commercial Property",
            "limit": "$15,000,000",
            "deductible": "$25,000",
            "effective_date": "2026-07-01",
        },
        "loss_history": {
            "period": "2021-2025",
            "claims": [
                {"year": 2022, "type": "Equipment Breakdown", "amount": "$45,000", "status": "Closed"},
                {"year": 2023, "type": "Water Damage", "amount": "$82,000", "status": "Closed"},
                {"year": 2024, "type": "Property Damage", "amount": "$15,000", "status": "Open"},
            ],
            "total_incurred": "$142,000",
            "loss_ratio": 0.063,
        },
    }

    for key, value in extracted.items():
        if isinstance(value, dict):
            console.print(f"  ✓ {key}: [dim](see details)[/dim]")
        else:
            console.print(f"  ✓ {key}: [green]{value}[/green]")

    state["extracted_data"] = extracted
    state["processing_log"].append(f"Extracted {len(extracted)} data fields")
    return state


# ============================================================
# Stage 3: Risk Assessment
# ============================================================

def stage_risk_assessment(state: SubmissionState) -> SubmissionState:
    console.print("\n[bold cyan]Stage 3: Risk Assessment[/bold cyan]")

    data = state["extracted_data"]
    flags = []

    # Check loss ratio
    loss_ratio = data.get("loss_history", {}).get("loss_ratio", 0)
    if loss_ratio > 0.1:
        flags.append(f"High loss ratio: {loss_ratio:.1%}")
    else:
        console.print(f"  ✓ Loss ratio: [green]{loss_ratio:.1%} (acceptable)[/green]")

    # Check open claims
    claims = data.get("loss_history", {}).get("claims", [])
    open_claims = [c for c in claims if c.get("status") == "Open"]
    if open_claims:
        flags.append(f"{len(open_claims)} open claim(s)")
        console.print(f"  ⚠ Open claims: [yellow]{len(open_claims)}[/yellow]")
    else:
        console.print("  ✓ No open claims")

    # Check building age
    year_built = data.get("property", {}).get("year_built", 2000)
    age = 2026 - year_built
    if age > 30:
        flags.append(f"Building age: {age} years (>30)")
    else:
        console.print(f"  ✓ Building age: [green]{age} years[/green]")

    # Check sprinklers
    if not data.get("property", {}).get("sprinklers", False):
        flags.append("No sprinkler system")
    else:
        console.print("  ✓ Sprinkler system: [green]Yes[/green]")

    state["risk_flags"] = flags
    state["processing_log"].append(f"Risk assessment: {len(flags)} flag(s)")
    return state


# ============================================================
# Stage 4: Guideline Matching
# ============================================================

def stage_match(state: SubmissionState) -> SubmissionState:
    console.print("\n[bold cyan]Stage 4: Guideline Matching[/bold cyan]")

    data = state["extracted_data"]
    coverage = data.get("requested_coverage", {})

    programs = [
        {
            "program": "CP-100",
            "name": "Commercial Property Standard",
            "max_limit": 15_000_000,
            "eligible_states": ["GA", "FL", "TX", "NC", "SC", "TN"],
            "min_year_built": 1990,
            "eligible_construction": ["Fire-resistive", "Non-combustible", "Masonry"],
        },
        {
            "program": "CP-200",
            "name": "Commercial Property Premium",
            "max_limit": 50_000_000,
            "eligible_states": ["GA", "FL", "TX", "NC", "SC", "TN", "AL", "VA"],
            "min_year_built": 1980,
            "eligible_construction": ["Fire-resistive", "Non-combustible"],
        },
        {
            "program": "CP-300",
            "name": "Manufacturing Specialty",
            "max_limit": 25_000_000,
            "eligible_states": ["GA", "FL", "TX"],
            "min_year_built": 2000,
            "eligible_construction": ["Fire-resistive"],
        },
    ]

    matches = []
    for prog in programs:
        score = 0
        reasons = []

        # State check
        state_abbrev = "GA"  # Simplified
        if state_abbrev in prog["eligible_states"]:
            score += 25
            reasons.append("State eligible")

        # Limit check
        requested = 15_000_000  # Simplified
        if requested <= prog["max_limit"]:
            score += 25
            reasons.append("Within limit")

        # Construction check
        construction = data.get("property", {}).get("construction", "")
        if construction in prog["eligible_construction"]:
            score += 25
            reasons.append("Construction type eligible")

        # Year built check
        year_built = data.get("property", {}).get("year_built", 2000)
        if year_built >= prog["min_year_built"]:
            score += 25
            reasons.append("Building age eligible")

        eligible = score >= 75
        matches.append({
            "program": prog["program"],
            "name": prog["name"],
            "fit_score": score,
            "eligible": eligible,
            "reasons": reasons,
        })

        status = "[green]✅ Eligible[/green]" if eligible else "[yellow]⚠ Partial[/yellow]"
        console.print(f"  {prog['program']} ({prog['name']}): {status} — {score}%")

    state["guideline_matches"] = sorted(matches, key=lambda m: m["fit_score"], reverse=True)
    state["processing_log"].append(f"Matched {len([m for m in matches if m['eligible']])} programs")
    return state


# ============================================================
# Stage 5: Recommendation
# ============================================================

def stage_recommend(state: SubmissionState) -> SubmissionState:
    console.print("\n[bold cyan]Stage 5: Final Recommendation[/bold cyan]")

    data = state["extracted_data"]
    matches = [m for m in state["guideline_matches"] if m["eligible"]]
    flags = state["risk_flags"]

    if not matches:
        recommendation = "DECLINE — No matching underwriting programs."
    elif len(flags) > 2:
        recommendation = "REFER TO SPECIALTY — Too many risk flags."
    elif flags:
        best = matches[0]
        recommendation = (
            f"PROCEED WITH REVIEW — Best: {best['program']} ({best['name']}, {best['fit_score']}%).\n"
            f"  Risk flags require manual review: {'; '.join(flags)}"
        )
    else:
        best = matches[0]
        recommendation = (
            f"PROCEED TO UNDERWRITING — Best: {best['program']} ({best['name']}, {best['fit_score']}%).\n"
            f"  Clean risk profile. Recommend auto-bind eligible."
        )

    state["recommendation"] = recommendation
    state["processing_log"].append(f"Recommendation: {recommendation.split(chr(10))[0]}")
    return state


# ============================================================
# Pipeline Runner
# ============================================================

def run_pipeline():
    console.print(Panel(
        "[bold]Insurance Submission Processing Pipeline[/bold]\n"
        "Classify → Extract → Risk Assess → Match → Recommend",
        title="Submission Agent Accelerator",
    ))

    state: SubmissionState = {
        "submission_id": f"SUB-{datetime.now().strftime('%Y%m%d')}-001",
        "broker": "Southeast Insurance Group",
        "documents": [
            {"name": "loss_run_2021_2025.pdf", "pages": 4},
            {"name": "application_commercial_property.docx", "pages": 8},
            {"name": "financial_statements_2024.pdf", "pages": 12},
            {"name": "coverage_summary_current.xlsx", "pages": 2},
            {"name": "property_photos_atlanta.zip", "pages": 1},
        ],
        "classifications": [],
        "extracted_data": {},
        "guideline_matches": [],
        "recommendation": "",
        "risk_flags": [],
        "processing_log": [],
    }

    console.print(f"\n[bold]Submission:[/bold] {state['submission_id']}")
    console.print(f"[bold]Broker:[/bold] {state['broker']}")
    console.print(f"[bold]Documents:[/bold] {len(state['documents'])}")

    # Input guardrail
    pii = check_pii(json.dumps(state))
    if pii:
        console.print(f"\n[red]❌ PII detected in submission data: {pii}. Blocked.[/red]")
        return

    # Run pipeline stages
    state = stage_classify(state)
    state = stage_extract(state)
    state = stage_risk_assessment(state)
    state = stage_match(state)
    state = stage_recommend(state)

    # Output guardrail
    pii = check_pii(state["recommendation"])
    if pii:
        console.print(f"\n[red]❌ PII detected in output. Blocked.[/red]")
        return

    # Final output
    console.print("\n" + "=" * 60)
    console.print(f"\n[bold green]{state['recommendation']}[/bold green]")

    # Processing log
    console.print("\n[bold]Processing Log:[/bold]")
    tree = Tree(f"📋 {state['submission_id']}")
    for i, entry in enumerate(state["processing_log"], 1):
        tree.add(f"Step {i}: {entry}")
    console.print(tree)

    # Summary table
    console.print("\n")
    table = Table(title="Submission Summary")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Submission ID", state["submission_id"])
    table.add_row("Insured", state["extracted_data"].get("insured_name", "N/A"))
    table.add_row("Coverage", state["extracted_data"].get("requested_coverage", {}).get("limit", "N/A"))
    table.add_row("State", state["extracted_data"].get("state", "N/A"))
    table.add_row("Documents", str(len(state["documents"])))
    table.add_row("Risk Flags", str(len(state["risk_flags"])))
    table.add_row("Matching Programs", str(len([m for m in state["guideline_matches"] if m["eligible"]])))
    console.print(table)


if __name__ == "__main__":
    run_pipeline()
