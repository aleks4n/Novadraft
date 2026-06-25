"""Pydantic schemas for research gaps — both the LLM output and the stored record."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

GapType = Literal[
    "limitation",
    "future_work",
    "caveat",
    "negative_result",
    "open_problem",
    "assumption",
    "threat_to_validity",
]

GAP_TYPES: list[str] = list(GapType.__args__)  # type: ignore[attr-defined]


# --- LLM output schema (what the gap agent returns) --------------------------

class LLMGapCandidate(BaseModel):
    title: str
    description: str
    type: GapType
    canonical_description: str
    evidence_section: str
    anchor: str


class LLMGapList(BaseModel):
    gaps: list[LLMGapCandidate] = Field(default_factory=list)

    @classmethod
    def prompt_schema(cls, available_sections: list[str]) -> str:
        types_str = ", ".join(GAP_TYPES)
        sections_str = ", ".join(available_sections)
        return (
            "Return JSON with this exact shape:\n\n"
            "{\n"
            '  "gaps": [\n'
            "    {\n"
            '      "title": string,                  // short noun phrase, <80 chars\n'
            '      "description": string,            // 1-3 sentences, self-contained\n'
            f'      "type": one of [{types_str}],\n'
            '      "canonical_description": string,  // normalized dedup key\n'
            f'      "evidence_section": one of [{sections_str}],\n'
            '      "anchor": string                  // 3-10 distinctive words, verbatim '
            'from the section, unique within it\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            'If the paper states no gaps, return {"gaps": []}.\n'
        )


# --- Stored gap record (written to gaps/pending/<gap_id>.json) ---------------

GroundingStatus = Literal["matched", "ambiguous", "not_found"]


class GapEvidence(BaseModel):
    paper_id: str
    section: str
    anchor: str
    quote: str
    char_offset: Optional[list[int]] = None
    grounding_status: GroundingStatus = "not_found"
    ambiguous_offsets: Optional[list[list[int]]] = None


class GapRecord(BaseModel):
    gap_id: str
    status: str = "unchecked"
    title: str
    description: str
    type: GapType
    canonical_description: str
    evidence: list[GapEvidence]
    extracted_at: str
