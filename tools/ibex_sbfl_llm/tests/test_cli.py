from ibex_sbfl_llm.cli import build_parser


def _rerank_help() -> str:
    parser = build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if action.__class__.__name__ == "_SubParsersAction"
    )
    return subparsers.choices["rerank"].format_help()


def test_include_reason_is_opt_in():
    parser = build_parser()
    default_args = parser.parse_args(["rerank", "rtl", "result", "--dry-run"])
    reasoned_args = parser.parse_args(
        ["rerank", "rtl", "result", "--dry-run", "--include-reason"]
    )
    assert default_args.include_reason is False
    assert default_args.structured_output == "auto"
    assert default_args.reasoning_effort == "high"
    assert default_args.api_protocol == "auto"
    assert default_args.temperature == 0.0
    assert reasoned_args.include_reason is True


def test_rerank_help_groups_options_and_shows_useful_defaults():
    help_text = _rerank_help()
    compact_help = " ".join(help_text.split())

    assert "inputs and patching:" in help_text
    assert "model and API:" in help_text
    assert "candidates and source context:" in help_text
    assert "output and diagnostics:" in help_text
    assert "--candidate-count N" in help_text
    assert "(default: 50)" in help_text
    assert "--llm-weight FLOAT" in help_text
    assert "1 uses only the LLM score" in compact_help
    assert "--ranking-strategy" not in help_text
    assert "(default: None)" not in help_text
    assert "Examples:" in help_text


def test_api_protocol_choices_use_explicit_provider_names():
    parser = build_parser()
    choices = (
        "auto",
        "anthropic",
        "zai",
        "openai-responses",
        "openai-chat-completions",
    )
    for protocol in choices:
        args = parser.parse_args(
            ["rerank", "rtl", "result", "--dry-run", "--api-protocol", protocol]
        )
        assert args.api_protocol == protocol
