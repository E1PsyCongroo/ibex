import json

from ibex_sbfl_rerank.artifacts import parse_prompt_candidates, parse_raw_assessments


def test_parse_prompt_and_raw_response(tmp_path) -> None:
    prompt = tmp_path / "llm_rerank.json.prompt.md"
    prompt.write_text(
        """# SBFL candidates

```json
[{"candidate_id":"B001","original_rank":1,"suspiciousness":"0.2","module":"m","scope":"s","bid":3,"block_type":"Assign","line_ranges":"4-5,7"}]
```

# Buggy RTL context
""",
        encoding="utf-8",
    )
    result_path = tmp_path / "llm_rerank.json"
    raw = {"assessments": [{"candidate_id": "B001", "score": 0.8}]}
    result = {"raw_model_response": f"```json\n{json.dumps(raw)}\n```"}

    candidates = parse_prompt_candidates(prompt)
    assessments = parse_raw_assessments(result, result_path)

    assert candidates[0].lines == (4, 5, 7)
    assert assessments == raw["assessments"]
