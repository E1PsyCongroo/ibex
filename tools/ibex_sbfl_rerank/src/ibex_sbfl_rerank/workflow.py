"""Directory-level offline reranking workflow."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ibex_sbfl_batch.statistics import summarize_llm

from . import __version__
from .artifacts import load_saved_result, parse_prompt_candidates, parse_raw_assessments
from .ranking import RerankError, rank_candidates


def default_output_filename(llm_weight: float) -> str:
    return f"llm_rerank.w{llm_weight:.12g}.json"


def discover_pairs(root: Path, input_filename: str) -> list[tuple[Path, Path]]:
    paths = [root / input_filename] if root.is_dir() else []
    paths.extend(root.rglob(input_filename) if root.is_dir() else [])
    unique_paths = sorted({path.resolve() for path in paths if path.is_file()})
    pairs: list[tuple[Path, Path]] = []
    for result_path in unique_paths:
        prompt_path = result_path.with_suffix(result_path.suffix + ".prompt.md")
        if not prompt_path.is_file():
            raise RerankError(f"missing prompt paired with {result_path}: {prompt_path}")
        pairs.append((result_path, prompt_path))
    if not pairs:
        raise RerankError(f"no {input_filename} and {input_filename}.prompt.md pairs under {root}")
    return pairs


def _positive_int(value: object, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise RerankError(f"{label} must be an integer") from exc
    if number <= 0:
        raise RerankError(f"{label} must be positive")
    return number


def rerank_pair(
    result_path: Path,
    prompt_path: Path,
    output_path: Path,
    llm_weight: float,
    requested_top_k: int | None,
) -> None:
    result = load_saved_result(result_path)
    candidates = parse_prompt_candidates(prompt_path)
    raw_assessments = parse_raw_assessments(result, result_path)
    assessments = rank_candidates(candidates, raw_assessments, llm_weight)

    config = result.get("config")
    output_config = dict(config) if isinstance(config, Mapping) else {}
    output_config.pop("ranking_strategy", None)
    source_top_k = result.get("top_k", output_config.get("top_k", len(candidates)))
    top_k = requested_top_k if requested_top_k is not None else _positive_int(source_top_k, "top_k")
    if top_k > len(assessments):
        raise RerankError(
            f"top_k={top_k} exceeds {len(assessments)} candidates in {prompt_path}"
        )
    output_config.update(
        {
            "candidate_count": len(candidates),
            "top_k": top_k,
            "llm_weight": llm_weight,
            "normalize_suspiciousness": False,
        }
    )
    output: dict[str, Any] = dict(result)
    output.update(
        {
            "schema_version": result.get("schema_version", 3),
            "tool_version": __version__,
            "config": output_config,
            "candidate_count": len(candidates),
            "top_k": top_k,
            "assessments": assessments,
            "rankings": [dict(item) for item in assessments[:top_k]],
            "offline_rerank": {
                "source_result": str(result_path),
                "source_prompt": str(prompt_path),
                "score_source": "raw_model_response",
                "suspiciousness_normalized": False,
            },
        }
    )
    _atomic_write_json(output_path, output)


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def find_bugset_root(logs_root: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if not candidate.is_dir():
            raise RerankError(f"bugset root is not a directory: {candidate}")
        return candidate
    search_roots = [Path.cwd().resolve(), logs_root, *logs_root.parents]
    for parent in search_roots:
        candidate = parent / "verify_dataset"
        if candidate.is_dir():
            return candidate.resolve()
    raise RerankError("cannot find verify_dataset; pass --bugset-root explicitly")


def run_directory(
    logs_root: Path,
    *,
    llm_weight: float,
    input_filename: str,
    output_filename: str,
    top_k: int | None,
    bugset_root: Path,
    summary_path: Path,
    line_window: int,
) -> int:
    pairs = discover_pairs(logs_root, input_filename)
    for index, (result_path, prompt_path) in enumerate(pairs, 1):
        output_path = result_path.parent / output_filename
        rerank_pair(
            result_path,
            prompt_path,
            output_path,
            llm_weight,
            top_k,
        )
        print(f"[RERANK {index}/{len(pairs)}] {output_path}")
    print(f"[DONE] reranked {len(pairs)} result(s) with llm_weight={llm_weight}")
    summarize_llm(bugset_root, logs_root, summary_path, line_window, output_filename)
    return 0
