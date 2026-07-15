import json

import pytest

from ibex_sbfl_llm.errors import SummaryError
from ibex_sbfl_llm.result_io import load_rerank_result


def ranking(rank=1):
    return {
        "candidate_id": "B001",
        "module": "m",
        "scope": "s",
        "bid": 1,
        "lines": [10],
        "reranked_rank": rank,
    }


def test_loads_legacy_and_v2_results(tmp_path):
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"top_k": 1, "rankings": [ranking()]}))
    assert load_rerank_result(legacy)["top_k"] == 1

    version2 = tmp_path / "v2.json"
    version2.write_text(
        json.dumps({"schema_version": 2, "config": {"top_k": 1}, "rankings": [ranking()]})
    )
    assert load_rerank_result(version2)["schema_version"] == 2


def test_rejects_noncontiguous_rank(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"top_k": 1, "rankings": [ranking(2)]}))
    with pytest.raises(SummaryError, match="contiguous"):
        load_rerank_result(path)
