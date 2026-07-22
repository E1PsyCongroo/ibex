"""Load Markdown prompts and assemble model input without ground-truth leakage."""

from __future__ import annotations

import json
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path

from .io_utils import read_text, sha256_text
from .models import Candidate
from .snippets import SourceBundle, compress_line_ranges

GENERIC_TEST_INFO = (
    "Differential processor testing observed an architectural mismatch. "
    "No more specific failure report was supplied; infer cautiously from the SBFL evidence "
    "and the provided RTL, and use `insufficient_evidence` when appropriate."
)


def load_prompt(name: str) -> str:
    return files("ibex_sbfl_llm.prompts").joinpath(name).read_text(encoding="utf-8")


def resolve_test_info(
    sbfl_dir: Path,
    explicit_text: str | None,
    explicit_file: Path | None,
) -> tuple[str, str]:
    if explicit_text:
        return explicit_text.strip(), "command_line"
    if explicit_file is not None:
        path = explicit_file.resolve()
        return read_text(path).strip(), str(path)
    for name in ("test_info.md", "test_info.txt", "test_info.json"):
        path = sbfl_dir / name
        if path.is_file():
            return read_text(path).strip(), str(path)
    return GENERIC_TEST_INFO, "generic"


def compact_candidate(candidate: Candidate) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "original_rank": candidate.original_rank,
        "suspiciousness": candidate.suspiciousness,
        "module": candidate.module,
        "scope": candidate.scope,
        "bid": candidate.bid,
        "block_type": candidate.block_type,
        "line_ranges": compress_line_ranges(candidate.lines),
    }


def build_user_prompt(
    candidates: Sequence[Candidate],
    sources: SourceBundle,
    test_info: str,
) -> str:
    sections: list[str] = []
    for region in sources.regions:
        mappings = json.dumps(region["candidate_ranges"], ensure_ascii=False, sort_keys=True)
        sections.append(
            f"## Module `{region['module']}` (`{Path(str(region['path'])).name}`)\n\n"
            f"Candidate line mappings: `{mappings}`\n\n"
            f"```systemverilog\n{region['source']}\n```"
        )
    replacements = {
        "test_info": test_info,
        "candidates_json": json.dumps(
            [compact_candidate(candidate) for candidate in candidates],
            ensure_ascii=False,
            indent=2,
        ),
        "source_sections": "\n\n".join(sections),
    }
    prompt = load_prompt("rerank.md")
    for key, value in replacements.items():
        prompt = prompt.replace(f"{{{key}}}", value)
    return prompt


def build_repair_prompt(validation_error: str, candidates: Sequence[Candidate]) -> str:
    prompt = load_prompt("repair.md")
    return prompt.replace("{validation_error}", validation_error).replace(
        "{candidate_ids}", ", ".join(candidate.candidate_id for candidate in candidates)
    )


def prompt_metadata(system_prompt: str, user_prompt: str) -> dict[str, str]:
    return {
        "system_prompt_sha256": sha256_text(system_prompt),
        "user_prompt_sha256": sha256_text(user_prompt),
        "prompt_sha256": sha256_text(system_prompt + "\n\n" + user_prompt),
    }
