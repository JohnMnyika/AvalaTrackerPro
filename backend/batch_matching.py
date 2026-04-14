from __future__ import annotations

import logging
import re
from typing import Iterable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_SEPARATOR_RE = re.compile(r"[-_]+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9-]+")
_DUPLICATE_HYPHEN_RE = re.compile(r"-{2,}")
_BATCH_ANCHOR_RE = re.compile(r"\bbatch-(\d+)\b")
_BATCH_SUFFIX_DIGITS_RE = re.compile(r"^(batch-\d+(?:-[a-z]+)+)\d{1,2}$")
_TRAILING_TIME_WORDS_RE = re.compile(r"(?:about|ago|hours?|days?|minutes?|months?)$")


def normalize_batch_name(name: str | None) -> str:
    if not name:
        return ""
    normalized = name.strip().lower()
    normalized = normalized.replace("_", "-")
    normalized = _NON_ALNUM_RE.sub("-", normalized)
    normalized = _SEPARATOR_RE.sub("-", normalized)
    normalized = re.sub(r"(?:-images|_images)$", "", normalized)
    normalized = _DUPLICATE_HYPHEN_RE.sub("-", normalized)
    normalized = _BATCH_SUFFIX_DIGITS_RE.sub(r"\1", normalized)
    while True:
        next_value = _TRAILING_TIME_WORDS_RE.sub("", normalized).strip("- ")
        if next_value == normalized:
            break
        normalized = next_value
    return normalized.strip("- ")


def extract_batch_anchor(name: str | None) -> str | None:
    normalized = normalize_batch_name(name)
    match = _BATCH_ANCHOR_RE.search(normalized)
    if not match:
        return None
    return f"batch-{match.group(1)}"


def find_best_normalized_match(
    source_name: str | None,
    candidates: Iterable[T],
    *,
    get_name,
) -> Optional[T]:
    normalized_source = normalize_batch_name(source_name)
    if not normalized_source:
        return None

    exact_matches = [candidate for candidate in candidates if normalize_batch_name(get_name(candidate)) == normalized_source]
    if exact_matches:
        return exact_matches[0]

    anchor = extract_batch_anchor(normalized_source)
    if not anchor:
        return None

    anchor_matches = [candidate for candidate in candidates if extract_batch_anchor(get_name(candidate)) == anchor]
    if len(anchor_matches) == 1:
        logger.info("Fuzzy matched %s via anchor %s", source_name, anchor)
        return anchor_matches[0]
    return None
