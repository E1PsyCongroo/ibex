from pathlib import Path

from ibex_sbfl_batch.info import format_info_value


def test_format_info_value_is_unambiguous() -> None:
    assert format_info_value(None) == "<none>"
    assert format_info_value(True) == "true"
    assert format_info_value(False) == "false"
    assert format_info_value([]) == "<empty>"
    assert format_info_value(["--name", "two words"]) == "--name 'two words'"
    assert format_info_value(Path("somewhere")) == "somewhere"
