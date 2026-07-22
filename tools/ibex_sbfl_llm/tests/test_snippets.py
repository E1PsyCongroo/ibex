from pathlib import Path

import pytest

from ibex_sbfl_llm.errors import SbflLlmError
from ibex_sbfl_llm.models import Candidate
from ibex_sbfl_llm.snippets import collect_sources, compress_line_ranges


def candidate(lines=(2, 4)):
    return Candidate("B001", 1, "0.5", "demo", "TOP.demo", 7, lines, "Always(COMB)")


def test_compress_line_ranges():
    assert compress_line_ranges([5, 2, 3, 3, 8]) == "2-3,5,8"


def test_snippet_removes_blank_lines_and_preserves_numbers(tmp_path: Path):
    (tmp_path / "demo.sv").write_text("one\n\nthree\nfour\n\nfive\n")
    bundle = collect_sources(tmp_path, [candidate()], "snippets", 1000, 1)
    source = str(bundle.regions[0]["source"])
    assert "   2:" not in source
    assert "   3: three" in source
    assert "   4: four" in source
    assert bundle.effective_radius == 1


def test_adaptively_reduces_radius(tmp_path: Path):
    (tmp_path / "demo.sv").write_text("\n".join(f"line{i}" for i in range(1, 21)))
    bundle = collect_sources(tmp_path, [candidate((10,))], "snippets", 35, 5)
    assert bundle.effective_radius < 5


def test_candidate_line_out_of_range_is_rejected(tmp_path: Path):
    (tmp_path / "demo.sv").write_text("one\n")
    with pytest.raises(SbflLlmError, match="outside source range"):
        collect_sources(tmp_path, [candidate((2,))], "snippets", 100, 0)
