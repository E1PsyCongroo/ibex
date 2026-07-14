#!/usr/bin/env python3
"""Use an LLM to rerank suspicious blocks produced by SBFL.

The script intentionally uses only Python's standard library.  It talks to an
OpenAI-compatible ``/chat/completions`` endpoint and validates that every item
returned by the model belongs to the original SBFL candidate set.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


BLOCK_RANK_RE = re.compile(
    r"^top-(?P<rank>\d+):\s+"
    r"Block\(scope:\s+(?P<scope>.*),\s+bid:\s+(?P<bid>\d+)\)\s+"
    r"with suspicious\s+'(?P<suspiciousness>[^']+)'"
)

SYSTEM_PROMPT = """\
You are a senior RTL verification and processor-debug engineer. Rerank the
given SBFL candidates by how likely each block is to contain the root-cause RTL
bug. Use both the statistical suspiciousness and the supplied SystemVerilog
source. Prefer causal control/data-path logic over downstream symptoms. Pay
attention to boundary conditions, incorrect predicates, state transitions,
width/signedness errors, handshake logic, and inconsistent behavior between
related branches. Do not invent candidates or source code.

Return JSON only, with exactly this shape:
{"rankings":[{"candidate_id":"B001","reason":"concise technical reason"}]}
The rankings array must contain exactly the requested number of distinct IDs,
ordered most suspicious first. Each ID must come from the candidate list.
"""


class RerankError(RuntimeError):
    """A user-facing input, API, or model-response error."""


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_ranked_blocks(result_log: Path, limit: int) -> list[dict[str, Any]]:
    """Parse the ``Suspiciousness of block`` section of result.log."""
    ranked: list[dict[str, Any]] = []
    in_section = False

    for raw_line in read_text(result_log).splitlines():
        line = raw_line.strip()
        if line == "Suspiciousness of block:":
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("Suspiciousness of "):
            break

        match = BLOCK_RANK_RE.match(line)
        if match is None:
            continue
        ranked.append(
            {
                "original_rank": int(match.group("rank")),
                "scope": match.group("scope"),
                "bid": int(match.group("bid")),
                "suspiciousness": match.group("suspiciousness"),
            }
        )
        if len(ranked) >= limit:
            break

    if not ranked:
        raise RerankError(f"no block ranking found in {result_log}")
    return ranked


def load_block_map(blocks_json: Path) -> dict[tuple[str, int], dict[str, Any]]:
    try:
        data = json.loads(read_text(blocks_json))
    except json.JSONDecodeError as exc:
        raise RerankError(f"invalid JSON in {blocks_json}: {exc}") from exc

    if not isinstance(data, list):
        raise RerankError(f"{blocks_json} must contain a JSON array")

    block_map: dict[tuple[str, int], dict[str, Any]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            key = (str(item["scope"]), int(item["bid"]))
        except (KeyError, TypeError, ValueError):
            continue
        block_map[key] = item
    return block_map


def enrich_candidates(
    ranked: Sequence[Mapping[str, Any]],
    block_map: Mapping[tuple[str, int], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    missing: list[str] = []

    for item in ranked:
        key = (str(item["scope"]), int(item["bid"]))
        block = block_map.get(key)
        if block is None:
            missing.append(f"scope={key[0]!r}, bid={key[1]}")
            continue
        try:
            lines = sorted({int(line) for line in block.get("lines", [])})
        except (TypeError, ValueError) as exc:
            raise RerankError(f"invalid lines for block {key}: {exc}") from exc

        candidate = dict(item)
        candidate.update(
            {
                "candidate_id": f"B{int(item['original_rank']):03d}",
                "module": str(block.get("module", "")),
                "lines": lines,
                "block_type": str(block.get("type", "")),
            }
        )
        if not candidate["module"]:
            raise RerankError(f"block {key} has no module in blocks.json")
        candidates.append(candidate)

    if missing:
        preview = "; ".join(missing[:3])
        suffix = " ..." if len(missing) > 3 else ""
        print(
            f"[WARN] skipped {len(missing)} ranked blocks absent from blocks.json: "
            f"{preview}{suffix}",
            file=sys.stderr,
        )
    if not candidates:
        raise RerankError("none of the ranked blocks exists in blocks.json")
    return candidates


def find_module_source(rtl_root: Path, module: str) -> Path:
    direct = rtl_root / f"{module}.sv"
    if direct.is_file():
        return direct

    matches = sorted(rtl_root.rglob(f"{module}.sv"))
    if not matches:
        raise RerankError(f"cannot find source for module {module!r} under {rtl_root}")
    if len(matches) > 1:
        names = ", ".join(str(path) for path in matches[:3])
        raise RerankError(f"multiple source files found for module {module!r}: {names}")
    return matches[0]


def numbered_source(path: Path) -> str:
    lines = read_text(path).splitlines()
    width = max(4, len(str(len(lines))))
    return "\n".join(f"{number:{width}d}: {line}" for number, line in enumerate(lines, 1))


def source_snippet(path: Path, interesting_lines: Iterable[int], radius: int) -> str:
    lines = read_text(path).splitlines()
    selected: set[int] = set()
    for line in interesting_lines:
        start = max(1, int(line) - radius)
        end = min(len(lines), int(line) + radius)
        selected.update(range(start, end + 1))

    width = max(4, len(str(len(lines))))
    output: list[str] = []
    previous = 0
    for number in sorted(selected):
        if previous and number != previous + 1:
            output.append("...")
        output.append(f"{number:{width}d}: {lines[number - 1]}")
        previous = number
    return "\n".join(output)


def collect_sources(
    rtl_root: Path,
    candidates: Sequence[Mapping[str, Any]],
    max_chars: int,
    snippet_radius: int,
) -> tuple[list[dict[str, str]], bool]:
    """Collect full implicated files, falling back to candidate-centered snippets."""
    module_lines: dict[str, set[int]] = defaultdict(set)
    for item in candidates:
        module_lines[str(item["module"])].update(int(x) for x in item["lines"])

    paths = {module: find_module_source(rtl_root, module) for module in module_lines}
    full_sources = [
        {"module": module, "path": str(paths[module]), "source": numbered_source(paths[module])}
        for module in sorted(paths)
    ]
    full_size = sum(len(item["source"]) for item in full_sources)
    if full_size <= max_chars:
        return full_sources, False

    snippets = [
        {
            "module": module,
            "path": str(paths[module]),
            "source": source_snippet(paths[module], module_lines[module], snippet_radius),
        }
        for module in sorted(paths)
    ]
    snippet_size = sum(len(item["source"]) for item in snippets)
    if snippet_size > max_chars:
        raise RerankError(
            f"candidate source snippets need {snippet_size} characters, exceeding "
            f"--max-source-chars={max_chars}; reduce --candidate-count or "
            "--snippet-radius, or increase the limit"
        )
    return snippets, True


def build_user_prompt(
    candidates: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, str]],
    top_k: int,
    used_snippets: bool,
) -> str:
    candidate_fields = (
        "candidate_id",
        "original_rank",
        "suspiciousness",
        "module",
        "lines",
        "block_type",
        "scope",
        "bid",
    )
    compact_candidates = [
        {key: item[key] for key in candidate_fields} for item in candidates
    ]
    source_label = "candidate-centered snippets" if used_snippets else "full files"
    source_sections = []
    for item in sources:
        source_sections.append(
            f"### module={item['module']} path={item['path']}\n```systemverilog\n"
            f"{item['source']}\n```"
        )

    return (
        f"Rerank the following {len(candidates)} SBFL block candidates and return "
        f"exactly the Top {top_k}. The initial rank is evidence, not ground truth.\n\n"
        "## SBFL candidates\n"
        f"{json.dumps(compact_candidates, ensure_ascii=False, indent=2)}\n\n"
        f"## RTL source ({source_label}; source line numbers are prefixed)\n"
        + "\n\n".join(source_sections)
    )


def endpoint_url(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def call_chat_completions(
    *,
    api_base: str,
    api_key: str | None,
    model: str,
    prompt: str,
    timeout: float,
    temperature: float | None,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    # Some reasoning models reject temperature entirely.  Only send it when
    # the user explicitly requests a value.
    if temperature is not None:
        payload["temperature"] = temperature
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        endpoint_url(api_base), data=body, headers=headers, method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RerankError(f"LLM API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RerankError(f"cannot reach LLM API: {exc.reason}") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise RerankError(f"invalid or timed-out LLM API response: {exc}") from exc

    try:
        content = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RerankError(
            "LLM API response has no choices[0].message.content: "
            + json.dumps(response_data, ensure_ascii=False)[:2000]
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise RerankError("LLM returned empty content")
    return content


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise RerankError("model response does not contain a JSON object")
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise RerankError(f"model returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RerankError("model response JSON must be an object")
    return value


def validate_rankings(
    response_text: str,
    candidates: Sequence[Mapping[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    value = extract_json_object(response_text)
    rankings = value.get("rankings")
    if not isinstance(rankings, list):
        raise RerankError("model response must contain a rankings array")
    if len(rankings) != top_k:
        raise RerankError(f"model returned {len(rankings)} rankings; expected {top_k}")

    candidate_map = {str(item["candidate_id"]): item for item in candidates}
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for new_rank, model_item in enumerate(rankings, 1):
        if not isinstance(model_item, dict):
            raise RerankError(f"ranking {new_rank} must be an object")
        candidate_id = str(model_item.get("candidate_id", ""))
        if candidate_id not in candidate_map:
            raise RerankError(f"model returned unknown candidate_id {candidate_id!r}")
        if candidate_id in seen:
            raise RerankError(f"model returned duplicate candidate_id {candidate_id!r}")
        seen.add(candidate_id)
        reason = model_item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise RerankError(f"ranking {candidate_id} has no reason")

        enriched = dict(candidate_map[candidate_id])
        enriched["reranked_rank"] = new_rank
        enriched["reason"] = reason.strip()
        result.append(enriched)
    return result


def resolve_input_files(sbfl_path: Path) -> tuple[Path, Path, Path]:
    if sbfl_path.is_dir():
        result_log = sbfl_path / "result.log"
        blocks_json = sbfl_path / "blocks.json"
        output_dir = sbfl_path
    elif sbfl_path.name == "result.log":
        result_log = sbfl_path
        blocks_json = sbfl_path.with_name("blocks.json")
        output_dir = sbfl_path.parent
    else:
        raise RerankError("SBFL path must be a result directory or a result.log file")

    for path in (result_log, blocks_json):
        if not path.is_file():
            raise RerankError(f"required input file does not exist: {path}")
    return result_log, blocks_json, output_dir


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use an OpenAI-compatible LLM to rerank SBFL block results."
    )
    parser.add_argument("rtl_source", type=Path, help="RTL source directory")
    parser.add_argument(
        "sbfl_result", type=Path, help="directory containing result.log and blocks.json"
    )
    parser.add_argument("--model", help="model name (required unless --dry-run)")
    parser.add_argument(
        "--api-base",
        default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        help="OpenAI-compatible API base (default: OPENAI_BASE_URL or %(default)s)",
    )
    parser.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="environment variable containing the API key; empty value allows no-auth APIs",
    )
    parser.add_argument("--candidate-count", type=int, default=50)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--max-source-chars", type=int, default=240_000)
    parser.add_argument("--snippet-radius", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--temperature",
        type=float,
        help="sampling temperature; omitted by default for reasoning-model compatibility",
    )
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    parser.add_argument("--output", type=Path, help="default: <SBFL dir>/llm_rerank.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate inputs and print the complete prompt without calling the API",
    )
    args = parser.parse_args(argv)

    if args.candidate_count <= 0 or args.top_k <= 0:
        parser.error("--candidate-count and --top-k must be positive")
    if args.top_k > args.candidate_count:
        parser.error("--top-k cannot exceed --candidate-count")
    if args.max_source_chars <= 0 or args.snippet_radius < 0:
        parser.error("source limits must be non-negative and max-source-chars must be positive")
    if args.retries < 0 or args.timeout <= 0:
        parser.error("--retries must be non-negative and --timeout must be positive")
    if not args.dry_run and not args.model:
        parser.error("--model is required unless --dry-run is used")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        rtl_root = args.rtl_source.resolve()
        if not rtl_root.is_dir():
            raise RerankError(f"RTL source directory does not exist: {rtl_root}")
        result_log, blocks_json, output_dir = resolve_input_files(args.sbfl_result.resolve())
        ranked = parse_ranked_blocks(result_log, args.candidate_count)
        candidates = enrich_candidates(ranked, load_block_map(blocks_json))
        if len(candidates) < args.top_k:
            raise RerankError(
                f"only {len(candidates)} valid candidates remain; cannot produce Top {args.top_k}"
            )
        sources, used_snippets = collect_sources(
            rtl_root, candidates, args.max_source_chars, args.snippet_radius
        )
        prompt = build_user_prompt(candidates, sources, args.top_k, used_snippets)

        if args.dry_run:
            print(SYSTEM_PROMPT)
            print("\n--- USER PROMPT ---\n")
            print(prompt)
            return 0

        api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
        if args.api_key_env and api_key is None:
            raise RerankError(
                f"API key environment variable {args.api_key_env!r} is not set; "
                "set it or pass --api-key-env '' for a no-auth endpoint"
            )

        response_text = ""
        attempt_prompt = prompt
        rankings: list[dict[str, Any]] | None = None
        last_error: RerankError | None = None
        llm_started = time.monotonic()
        for attempt in range(args.retries + 1):
            response_text = ""
            try:
                response_text = call_chat_completions(
                    api_base=args.api_base,
                    api_key=api_key,
                    model=args.model,
                    prompt=attempt_prompt,
                    timeout=args.timeout,
                    temperature=args.temperature,
                )
                rankings = validate_rankings(response_text, candidates, args.top_k)
                break
            except RerankError as exc:
                last_error = exc
                if attempt >= args.retries:
                    break
                # If the endpoint answered but the content was malformed, tell
                # the model what to correct on the next attempt.  This text is
                # harmless for transport errors and avoids retrying an invalid
                # deterministic answer verbatim.
                if response_text:
                    attempt_prompt = (
                        prompt
                        + "\n\n## Correction required\n"
                        + f"Your previous answer was rejected: {exc}. Return corrected JSON only."
                    )
                print(
                    f"[WARN] attempt {attempt + 1} failed: {exc}; retrying...",
                    file=sys.stderr,
                )
                time.sleep(args.retry_delay * (attempt + 1))
        if rankings is None:
            assert last_error is not None
            raise last_error
        llm_elapsed_seconds = time.monotonic() - llm_started

        output_path = (args.output or (output_dir / "llm_rerank.json")).resolve()
        output = {
            "model": args.model,
            "result_log": str(result_log),
            "blocks_json": str(blocks_json),
            "rtl_source": str(rtl_root),
            "candidate_count": len(candidates),
            "top_k": args.top_k,
            "source_mode": "snippets" if used_snippets else "full_files",
            "llm_elapsed_seconds": round(llm_elapsed_seconds, 6),
            "rankings": rankings,
            "raw_model_response": response_text,
        }
        atomic_write_json(output_path, output)
        print(f"wrote {len(rankings)} reranked candidates to {output_path}")
        return 0
    except (OSError, RerankError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
