from pathlib import Path

from ibex_sbfl_llm.models import Candidate
from ibex_sbfl_llm.prompting import build_user_prompt, resolve_test_info
from ibex_sbfl_llm.snippets import SourceBundle


def test_prompt_contains_candidates_but_not_patch_metadata(tmp_path: Path):
    candidate = Candidate("B001", 1, "1.0", "demo", "TOP.demo", 1, (3,), "Assign")
    bundle = SourceBundle(
        regions=(
            {
                "module": "demo",
                "path": "/tmp/private/buggy/demo.sv",
                "candidate_ranges": {"B001": "3"},
                "source": "   3: assign x = y;",
            },
        ),
        source_mode="snippets",
        effective_radius=0,
        source_chars=18,
    )
    prompt = build_user_prompt([candidate], bundle, "architectural mismatch")
    assert "B001" in prompt
    assert "assign x = y" in prompt
    assert "/tmp/private" not in prompt
    assert "bug_info" not in prompt


def test_test_info_priority(tmp_path: Path):
    (tmp_path / "test_info.txt").write_text("file report")
    assert resolve_test_info(tmp_path, "explicit", None) == ("explicit", "command_line")
    text, source = resolve_test_info(tmp_path, None, None)
    assert text == "file report"
    assert source.endswith("test_info.txt")
