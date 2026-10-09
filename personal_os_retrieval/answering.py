from __future__ import annotations

import hashlib
import json
import re
from typing import Any


ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "claims", "conflicts", "missing_information"],
    "properties": {
        "status": {
            "type": "string",
            "enum": ["answered", "partially_answered", "insufficient_evidence", "conflicting_evidence"],
        },
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "kind", "evidence"],
                "properties": {
                    "text": {"type": "string", "minLength": 1, "maxLength": 600},
                    "kind": {"type": "string", "enum": ["confirmed_fact", "inference"]},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["evidence_id", "quote"],
                            "properties": {
                                "evidence_id": {"type": "string", "minLength": 1, "maxLength": 40},
                                "quote": {"type": "string", "minLength": 1, "maxLength": 500},
                            },
                        },
                    },
                },
            },
        },
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["issue", "evidence_ids"],
                "properties": {
                    "issue": {"type": "string", "minLength": 1, "maxLength": 300},
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 40},
                        "minItems": 2,
                    },
                },
            },
        },
        "missing_information": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
    },
}


_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_STATUSES = {"answered", "partially_answered", "insufficient_evidence", "conflicting_evidence"}
_KINDS = {"confirmed_fact", "inference"}


def evidence_identifier(path: str, heading: str, note_sha256: str) -> str:
    """Stable for a particular source version and section; changes when source bytes change."""
    raw = f"{path}\0{heading}\0{note_sha256}".encode("utf-8", errors="strict")
    return "ev_" + hashlib.sha256(raw).hexdigest()[:16]


def estimate_tokens(text: str) -> int:
    """Conservative tokenizer-independent estimate used only to cap supplied evidence."""
    if not text:
        return 0
    word_or_punctuation = len(_TOKEN_RE.findall(text))
    utf8_upper_bound = (len(text.encode("utf-8")) + 2) // 3
    return max(word_or_punctuation, utf8_upper_bound)


def truncate_to_token_budget(text: str, budget: int) -> str:
    if budget <= 0 or not text:
        return ""
    if estimate_tokens(text) <= budget:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(text[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    clipped = text[:low].rstrip()
    if clipped and low < len(text) and not text[low - 1].isspace() and not text[low].isspace():
        clipped = clipped.rsplit(" ", 1)[0] if " " in clipped else ""
    while clipped and estimate_tokens(clipped) > budget:
        clipped = clipped[:-1].rstrip()
    return clipped


def abstention(missing_information: list[str] | None = None) -> dict[str, Any]:
    missing = [str(item).strip() for item in (missing_information or []) if str(item).strip()]
    return {
        "status": "insufficient_evidence",
        "claims": [],
        "conflicts": [],
        "missing_information": missing[:10] or ["The available notes do not establish the requested fact."],
    }


def validate_answer(payload: Any, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Drop claims without a known source ID and an exact quote from that source."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return abstention(["The model response was not valid structured data."])
    if not isinstance(payload, dict):
        return abstention(["The model response was not a structured object."])

    evidence_by_id = {
        str(item.get("evidence_id")): item
        for item in evidence
        if isinstance(item, dict) and item.get("evidence_id") and isinstance(item.get("text"), str)
    }
    raw_status = payload.get("status")
    status = raw_status if raw_status in _STATUSES else "insufficient_evidence"
    checked_claims: list[dict[str, Any]] = []
    raw_claims = payload.get("claims", [])
    if isinstance(raw_claims, list):
        for claim in raw_claims[:20]:
            if not isinstance(claim, dict):
                continue
            text = str(claim.get("text", "")).strip()
            kind = claim.get("kind")
            if not text or len(text) > 600 or kind not in _KINDS:
                continue
            checked_refs: list[dict[str, str]] = []
            raw_refs = claim.get("evidence", [])
            if isinstance(raw_refs, list):
                for ref in raw_refs[:10]:
                    if not isinstance(ref, dict):
                        continue
                    evidence_id = str(ref.get("evidence_id", "")).strip()
                    quote = str(ref.get("quote", "")).strip()
                    source = evidence_by_id.get(evidence_id)
                    if source and quote and len(quote) <= 500 and quote in source["text"]:
                        checked_refs.append({"evidence_id": evidence_id, "quote": quote})
            if checked_refs:
                checked_claims.append({"text": text, "kind": kind, "evidence": checked_refs})

    checked_conflicts: list[dict[str, Any]] = []
    raw_conflicts = payload.get("conflicts", [])
    if isinstance(raw_conflicts, list):
        for conflict in raw_conflicts[:10]:
            if not isinstance(conflict, dict):
                continue
            issue = str(conflict.get("issue", "")).strip()
            ids = conflict.get("evidence_ids", [])
            if not isinstance(ids, list):
                continue
            ids = list(dict.fromkeys(str(item).strip() for item in ids if str(item).strip()))
            if issue and len(issue) <= 300 and len(ids) >= 2 and all(item in evidence_by_id for item in ids):
                checked_conflicts.append({"issue": issue, "evidence_ids": ids})

    raw_missing = payload.get("missing_information", [])
    missing: list[str] = []
    if isinstance(raw_missing, list):
        for item in raw_missing[:10]:
            value = str(item).strip()
            if value and len(value) <= 300 and value not in missing:
                missing.append(value)

    if checked_conflicts:
        status = "conflicting_evidence"
    elif status == "conflicting_evidence":
        status = "partially_answered" if checked_claims else "insufficient_evidence"
    if not checked_claims and not checked_conflicts:
        status = "insufficient_evidence"
    if status == "insufficient_evidence":
        checked_claims = []
        if not missing:
            missing = ["The available notes do not establish the requested fact."]
    elif missing and status == "answered":
        status = "partially_answered"

    return {
        "status": status,
        "claims": checked_claims,
        "conflicts": checked_conflicts,
        "missing_information": missing,
    }


def build_answer_response(
    answer: dict[str, Any], evidence: list[dict[str, Any]], *, method: str,
) -> dict[str, Any]:
    evidence_by_id = {str(item["evidence_id"]): item for item in evidence if item.get("evidence_id")}
    sources: dict[str, dict[str, Any]] = {}
    lines: list[str] = []
    for claim in answer.get("claims", []):
        citations: list[str] = []
        for ref in claim.get("evidence", []):
            evidence_id = ref["evidence_id"]
            source = evidence_by_id.get(evidence_id)
            if source is None:
                continue
            citations.append(evidence_id)
            sources[evidence_id] = {
                "path": source.get("vault_relative_path", source.get("path", "")),
                "heading": source.get("heading", ""),
                "modified": source.get("modified"),
                "knowledge_status": source.get("knowledge_status"),
                "quote": ref["quote"],
            }
        if citations:
            prefix = "Inference: " if claim.get("kind") == "inference" else ""
            lines.append(f"{prefix}{claim['text']} " + " ".join(f"[{item}]" for item in dict.fromkeys(citations)))

    for conflict in answer.get("conflicts", []):
        ids = [item for item in conflict["evidence_ids"] if item in evidence_by_id]
        for evidence_id in ids:
            source = evidence_by_id[evidence_id]
            sources.setdefault(evidence_id, {
                "path": source.get("vault_relative_path", source.get("path", "")),
                "heading": source.get("heading", ""),
                "modified": source.get("modified"),
                "knowledge_status": source.get("knowledge_status"),
            })
        if len(ids) >= 2:
            lines.append(f"Conflicting evidence: {conflict['issue']} " + " ".join(f"[{item}]" for item in ids))

    missing = answer.get("missing_information", [])
    if missing:
        lines.append("Not established in the available notes: " + "; ".join(missing))
    if not lines:
        lines.append("The available notes do not establish the requested fact.")

    return {
        "success": True,
        "status": answer.get("status", "insufficient_evidence"),
        "answer": "\n".join(lines),
        "claims": answer.get("claims", []),
        "conflicts": answer.get("conflicts", []),
        "missing_information": missing,
        "sources": sources,
        "retrieval_method": method,
        "quote_check_note": "Quotes are exact substring matches; this alone does not prove semantic entailment.",
    }
