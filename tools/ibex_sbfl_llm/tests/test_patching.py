from pathlib import Path

import pytest

from ibex_sbfl_llm.errors import PatchError
from ibex_sbfl_llm.patching import discover_patch, prepare_rtl_workspace

DIFF = """--- a/rtl/demo.sv
+++ b/rtl/demo.sv
@@ -1,3 +1,3 @@
 module demo;
-  assign value = good;
+  assign value = bad;
 endmodule
"""


def make_case(tmp_path: Path):
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "demo.sv").write_text("module demo;\n  assign value = good;\nendmodule\n")
    patch = tmp_path / "bug.diff"
    patch.write_text(DIFF)
    result = tmp_path / "result"
    result.mkdir()
    (result / "run.log").write_text(f"[DIFF] {patch}\n")
    return rtl, patch, result


def test_discovers_and_applies_patch_without_mutating_source(tmp_path: Path):
    rtl, patch, result = make_case(tmp_path)
    assert discover_patch(result / "run.log") == patch.resolve()
    with prepare_rtl_workspace(rtl, result, None, False) as workspace:
        assert workspace.patch_state == "applied"
        assert "assign value = bad" in (workspace.rtl_root / "demo.sv").read_text()
        assert workspace.changed_files == ("rtl/demo.sv",)
    assert "assign value = good" in (rtl / "demo.sv").read_text()


def test_recognizes_already_patched_source(tmp_path: Path):
    rtl, patch, result = make_case(tmp_path)
    (rtl / "demo.sv").write_text("module demo;\n  assign value = bad;\nendmodule\n")
    with prepare_rtl_workspace(rtl, result, patch, False) as workspace:
        assert workspace.patch_state == "already_patched"


def test_missing_patch_requires_explicit_override(tmp_path: Path):
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    result = tmp_path / "result"
    result.mkdir()
    with pytest.raises(PatchError, match="cannot discover"):
        with prepare_rtl_workspace(rtl, result, None, False):
            pass
    with prepare_rtl_workspace(rtl, result, None, True) as workspace:
        assert workspace.patch_state == "not_available"


def test_rejects_patch_outside_rtl(tmp_path: Path):
    rtl, patch, result = make_case(tmp_path)
    patch.write_text(DIFF.replace("rtl/demo.sv", "../demo.sv"))
    with pytest.raises(PatchError, match="unsafe path"):
        with prepare_rtl_workspace(rtl, result, patch, False):
            pass
