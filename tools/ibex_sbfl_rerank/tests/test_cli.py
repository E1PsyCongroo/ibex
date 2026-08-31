import pytest

from ibex_sbfl_rerank.cli import build_parser
from ibex_sbfl_rerank.workflow import default_output_filename


def test_cli_uses_only_weight_parameter() -> None:
    parser = build_parser()

    args = parser.parse_args(["logs", "--llm-weight", "1.0"])

    assert args.llm_weight == 1.0
    assert not hasattr(args, "strategy")
    assert default_output_filename(args.llm_weight) == "llm_rerank.w1.json"


def test_removed_strategy_option_is_rejected() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["logs", "--strategy", "weighted"])
