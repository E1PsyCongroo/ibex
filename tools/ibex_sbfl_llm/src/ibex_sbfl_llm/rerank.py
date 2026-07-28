"""End-to-end orchestration for LLM-assisted candidate reranking."""

from __future__ import annotations

import os
from typing import Any

from . import __version__
from .errors import SbflLlmError
from .io_utils import atomic_write_json, atomic_write_text, sha256_file
from .openai_client import call_model
from .patching import prepare_rtl_workspace
from .prompting import (
    build_user_prompt,
    load_prompt,
    prompt_metadata,
    resolve_test_info,
)
from .ranking import rank_candidates
from .sbfl import build_candidates, load_blocks, parse_block_suspiciousness, resolve_input_files
from .snippets import collect_sources


def run_rerank(args: Any) -> int:
    rtl_root = args.rtl_source.resolve()
    result_log, blocks_json, output_dir = resolve_input_files(args.sbfl_result.resolve())
    ranked_blocks = parse_block_suspiciousness(result_log)
    if not ranked_blocks:
        raise SbflLlmError(f"no block ranking found in {result_log}")
    candidates = build_candidates(ranked_blocks, load_blocks(blocks_json), args.candidate_count)
    if len(candidates) < args.top_k:
        raise SbflLlmError(
            f"only {len(candidates)} valid candidates remain; cannot produce Top {args.top_k}"
        )

    test_info, test_info_source = resolve_test_info(output_dir, args.test_info, args.test_info_file)
    output_path = (args.output or output_dir / "llm_rerank.json").resolve()

    with prepare_rtl_workspace(
        rtl_root=rtl_root,
        sbfl_dir=output_dir,
        explicit_patch=args.patch,
        allow_unpatched=args.allow_unpatched_source,
    ) as patch_workspace:
        sources = collect_sources(
            patch_workspace.rtl_root,
            candidates,
            args.source_mode,
            args.max_source_chars,
            args.snippet_radius,
        )
        system_prompt = load_prompt("system.md")
        include_reason = bool(getattr(args, "include_reason", False))
        user_prompt = build_user_prompt(
            candidates,
            sources,
            test_info,
            include_reason=include_reason,
        )
        hashes = prompt_metadata(system_prompt, user_prompt)

        if args.save_prompt:
            prompt_path = output_path.with_suffix(output_path.suffix + ".prompt.md")
            atomic_write_text(
                prompt_path,
                f"# System prompt\n\n{system_prompt}\n\n# User prompt\n\n{user_prompt}\n",
            )

        if args.dry_run:
            print(system_prompt)
            print("\n--- USER PROMPT ---\n")
            print(user_prompt)
            print("\n--- DRY-RUN METADATA ---")
            print(f"patch_state={patch_workspace.patch_state}")
            print(f"patch_path={patch_workspace.patch_path or ''}")
            print(f"source_mode={sources.source_mode}")
            print(f"effective_radius={sources.effective_radius}")
            print(f"source_chars={sources.source_chars}")
            print(f"prompt_sha256={hashes['prompt_sha256']}")
            return 0

        api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
        if args.api_key_env and api_key is None:
            raise SbflLlmError(
                f"API key environment variable {args.api_key_env!r} is not set; "
                "set it or pass --api-key-env '' for a no-auth compatible endpoint"
            )
        api_result = call_model(
            api_base=args.api_base,
            api_key=api_key,
            model=args.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            candidates=candidates,
            timeout=args.timeout,
            temperature=args.temperature,
            retries=args.retries,
            retry_delay=args.retry_delay,
            structured_output=args.structured_output,
            include_reason=include_reason,
        )
        assessments = rank_candidates(
            candidates,
            api_result.response,
            args.ranking_strategy,
            args.llm_weight,
        )
        rankings = [dict(item) for item in assessments[: args.top_k]]

        inputs = {
            "result_log": str(result_log),
            "result_log_sha256": sha256_file(result_log),
            "blocks_json": str(blocks_json),
            "blocks_json_sha256": sha256_file(blocks_json),
            "rtl_source": str(rtl_root),
            "patch_path": str(patch_workspace.patch_path) if patch_workspace.patch_path else None,
            "patch_sha256": patch_workspace.patch_sha256,
            "patch_state": patch_workspace.patch_state,
            "patch_changed_files": list(patch_workspace.changed_files),
            "patched_file_hashes": patch_workspace.after_hashes,
            "test_info_source": test_info_source,
        }
        config = {
            "candidate_count": len(candidates),
            "top_k": args.top_k,
            "source_mode": sources.source_mode,
            "requested_source_mode": args.source_mode,
            "snippet_radius": args.snippet_radius,
            "effective_snippet_radius": sources.effective_radius,
            "max_source_chars": args.max_source_chars,
            "source_chars": sources.source_chars,
            "ranking_strategy": args.ranking_strategy,
            "llm_weight": args.llm_weight,
            "structured_output": args.structured_output,
            "include_reason": include_reason,
            "temperature": args.temperature,
            "timeout": args.timeout,
            "retries": args.retries,
            "retry_delay": args.retry_delay,
        }
        request = {
            **hashes,
            "api": "responses",
            "structured_output": api_result.structured_output,
            "response_id": api_result.response_id,
            "attempts": api_result.attempts,
            "usage": api_result.usage,
            "elapsed_seconds": round(api_result.elapsed_seconds, 6),
        }
        output = {
            "schema_version": 3,
            "tool_version": __version__,
            "model": args.model,
            "inputs": inputs,
            "config": config,
            "request": request,
            # Compatibility fields consumed by sbfl_batch statistics.
            "result_log": str(result_log),
            "blocks_json": str(blocks_json),
            "rtl_source": str(rtl_root),
            "candidate_count": len(candidates),
            "top_k": args.top_k,
            "source_mode": sources.source_mode,
            "llm_elapsed_seconds": round(api_result.elapsed_seconds, 6),
            "assessments": assessments,
            "rankings": rankings,
            "raw_model_response": api_result.raw_text,
        }
        atomic_write_json(output_path, output)
        print(
            f"wrote {len(rankings)} reranked candidates and {len(assessments)} scored "
            f"assessments to {output_path}"
        )
        return 0
