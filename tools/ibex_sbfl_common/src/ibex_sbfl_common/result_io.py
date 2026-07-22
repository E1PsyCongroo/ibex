"""Read and validate schema-v1 and schema-v2 rerank result files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import SummaryError
from .io_utils import read_text


def load_rerank_result(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise SummaryError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SummaryError(f"{path} must contain a JSON object")
    rankings = value.get("rankings")
    if not isinstance(rankings, list) or not rankings:
        raise SummaryError(f"{path} must contain a non-empty rankings array")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_ranks: set[int] = set()
    required = ("candidate_id", "module", "scope", "bid", "lines", "reranked_rank")
    for index, raw in enumerate(rankings, 1):
        if not isinstance(raw, dict):
            raise SummaryError(f"ranking {index} in {path} is not an object")
        missing = [field for field in required if field not in raw]
        if missing:
            raise SummaryError(f"ranking {index} in {path} is missing: {', '.join(missing)}")
        if not isinstance(raw["lines"], list):
            raise SummaryError(f"ranking {index} in {path} has non-array lines")
        try:
            reranked_rank = int(raw["reranked_rank"])
            bid = int(raw["bid"])
            lines = sorted({int(line) for line in raw["lines"]})
        except (TypeError, ValueError) as exc:
            raise SummaryError(f"ranking {index} in {path} has invalid numbers") from exc
        candidate_id = str(raw["candidate_id"])
        if not candidate_id or candidate_id in seen_ids or reranked_rank in seen_ranks:
            raise SummaryError(f"duplicate or empty ranking identity at item {index} in {path}")
        seen_ids.add(candidate_id)
        seen_ranks.add(reranked_rank)
        item = dict(raw)
        item.update(
            {
                "candidate_id": candidate_id,
                "module": str(raw["module"]),
                "scope": str(raw["scope"]),
                "bid": bid,
                "lines": lines,
                "reranked_rank": reranked_rank,
            }
        )
        normalized.append(item)

    normalized.sort(key=lambda item: item["reranked_rank"])
    if [item["reranked_rank"] for item in normalized] != list(range(1, len(normalized) + 1)):
        raise SummaryError(f"reranked_rank values in {path} must be contiguous")
    try:
        top_k = int(value.get("top_k", value.get("config", {}).get("top_k", len(normalized))))
    except (TypeError, ValueError) as exc:
        raise SummaryError(f"top_k in {path} is not an integer") from exc
    if top_k != len(normalized):
        raise SummaryError(f"top_k={top_k} in {path}, but rankings has {len(normalized)} items")
    value["rankings"] = normalized
    value["top_k"] = top_k
    return value
