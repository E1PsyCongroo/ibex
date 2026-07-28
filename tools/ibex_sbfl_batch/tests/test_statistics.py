from ibex_sbfl_batch.statistics import LLM_FIELDS, compute_llm_stats, compute_sbfl_stats


def test_sbfl_timing_counts_only_present_values() -> None:
    stats = compute_sbfl_stats(
        [
            {
                "status": "OK",
                "top-k": "top-1",
                "gen_time": "2s",
                "sbfl_time": "",
            },
            {
                "status": "OK",
                "top-k": "over top-10",
                "gen_time": "",
                "sbfl_time": "500ms",
            },
        ]
    )
    assert stats["ok"] == 2
    assert stats["top1"] == 1
    assert stats["gen_count"] == 1
    assert stats["gen_sum"] == 2.0
    assert stats["sbfl_count"] == 1
    assert stats["sbfl_sum"] == 0.5


def test_llm_stats_compare_rerank_with_sbfl() -> None:
    stats = compute_llm_stats(
        [
            {
                "status": "OK",
                "model": "test",
                "top-k": "top-2",
                "sbfl_top-k": "top-5",
                "llm_elapsed_time": "1s",
            }
        ]
    )
    assert stats["ok"] == 1
    assert stats["top5"] == 1
    assert stats["improved"] == 1
    assert stats["mrr"] == 0.5


def test_llm_summary_keeps_optional_reason_but_not_removed_fields() -> None:
    assert "reason" in LLM_FIELDS
    assert "causal_role" not in LLM_FIELDS
    assert "key_lines" not in LLM_FIELDS
