from ibex_sbfl_batch.statistics import (
    LLM_FIELDS,
    compute_llm_stats,
    compute_sbfl_stats,
    print_llm_stats,
)


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


def test_llm_stats_average_tokens_counts_only_present_ok_values(capsys) -> None:
    rows = [
        {
            "status": "OK",
            "prompt_tokens": "100",
            "completion_tokens": "20",
            "total_tokens": "120",
        },
        {
            "status": "OK",
            "prompt_tokens": "200",
            "completion_tokens": "",
            "total_tokens": "240",
        },
        {
            "status": "LLM_ERROR",
            "prompt_tokens": "900",
            "completion_tokens": "900",
            "total_tokens": "1800",
        },
    ]

    stats = compute_llm_stats(rows)

    assert stats["average_prompt_tokens"] == 150.0
    assert stats["average_completion_tokens"] == 20.0
    assert stats["average_total_tokens"] == 180.0

    print_llm_stats(rows)
    output = capsys.readouterr().out
    assert "Average prompt tokens     : 150.00" in output
    assert "Average completion tokens : 20.00" in output
    assert "Average total tokens      : 180.00" in output


def test_llm_summary_keeps_optional_reason_but_not_removed_fields() -> None:
    assert "reason" in LLM_FIELDS
    assert "causal_role" not in LLM_FIELDS
    assert "key_lines" not in LLM_FIELDS
