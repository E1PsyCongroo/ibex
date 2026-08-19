import json
from argparse import Namespace
from pathlib import Path

from ibex_sbfl_llm.models import (
    ApiResult,
    AssessmentResponse,
    CandidateAssessment,
)
from ibex_sbfl_llm.rerank import run_rerank


def test_end_to_end_rerank_writes_schema_v3_with_patched_source(tmp_path: Path, monkeypatch):
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "demo.sv").write_text("module demo;\n  assign value = good;\nendmodule\n")
    patch = tmp_path / "bug.diff"
    patch.write_text(
        """--- a/rtl/demo.sv
+++ b/rtl/demo.sv
@@ -1,3 +1,3 @@
 module demo;
-  assign value = good;
+  assign value = bad;
 endmodule
"""
    )
    result = tmp_path / "result"
    result.mkdir()
    (result / "run.log").write_text(f"[DIFF] {patch}\n")
    (result / "result.log").write_text(
        "Suspiciousness of block:\n"
        "top-1: Block(scope: TOP.demo, bid: 7) with suspicious '0.500000'\n"
    )
    (result / "blocks.json").write_text(
        json.dumps(
            [
                {
                    "scope": "TOP.demo",
                    "bid": 7,
                    "module": "demo",
                    "lines": [2],
                    "type": "Assign",
                }
            ]
        )
    )

    model_response = AssessmentResponse(
        assessments=[
            CandidateAssessment(
                candidate_id="B001",
                score=0.9,
            )
        ]
    )

    def fake_call_model(**kwargs):
        assert "assign value = bad" in kwargs["user_prompt"]
        assert "assign value = good" not in kwargs["user_prompt"]
        assert kwargs["include_reason"] is False
        return ApiResult(model_response, "{}", "r1", {"total_tokens": 10}, "json_schema", 1, 0.1)

    monkeypatch.setattr("ibex_sbfl_llm.rerank.call_model", fake_call_model)
    output = result / "llm_rerank.json"
    args = Namespace(
        rtl_source=rtl,
        sbfl_result=result,
        model="test-model",
        api_base="http://localhost/v1",
        api_protocol="responses",
        api_key_env="",
        candidate_count=1,
        top_k=1,
        source_mode="snippets",
        max_source_chars=10_000,
        snippet_radius=1,
        patch=None,
        allow_unpatched_source=False,
        test_info="failure",
        test_info_file=None,
        ranking_strategy="weighted",
        llm_weight=0.75,
        structured_output="auto",
        include_reason=False,
        timeout=1.0,
        temperature=None,
        max_output_tokens=8192,
        reasoning_effort="high",
        retries=0,
        retry_delay=0.0,
        output=output,
        save_prompt=False,
        dry_run=False,
    )
    assert run_rerank(args) == 0
    value = json.loads(output.read_text())
    assert value["schema_version"] == 3
    assert value["inputs"]["patch_state"] == "applied"
    assert value["request"]["api"] == "responses"
    assert value["config"]["include_reason"] is False
    assert value["assessments"][0]["llm_score"] == 0.9
    assert value["rankings"][0]["reranked_rank"] == 1
    assert "reason" not in value["assessments"][0]
    assert "key_lines" not in value["assessments"][0]
    assert "causal_role" not in value["assessments"][0]
    assert "assign value = good" in (rtl / "demo.sv").read_text()
