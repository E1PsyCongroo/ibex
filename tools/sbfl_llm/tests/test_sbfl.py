from ibex_sbfl_llm.sbfl import find_bug_rank, iter_suspiciousness_tie_groups


def test_tie_average_and_boundary_rule():
    ranked = [
        {"rank": 1, "scope": "s", "bid": 1, "sus": "1.0"},
        {"rank": 2, "scope": "s", "bid": 2, "sus": "1.000"},
        {"rank": 3, "scope": "s", "bid": 3, "sus": "0.5"},
    ]
    groups = iter_suspiciousness_tie_groups(ranked)
    assert str(groups[0][1]) == "3/2"
    blocks = {
        ("s", 1): {"scope": "s", "module": "m", "lines": [10]},
        ("s", 2): {"scope": "s", "module": "m", "lines": [20]},
        ("s", 3): {"scope": "s", "module": "m", "lines": [30]},
    }
    bug = {"module_name": "m", "scope_name": "s", "modify_line": [20]}
    assert find_bug_rank(bug, ranked, blocks) == ("top-1.5", "1.000")


def test_all_tied_is_over():
    ranked = [
        {"rank": 1, "scope": "s", "bid": 1, "sus": "1"},
        {"rank": 2, "scope": "s", "bid": 2, "sus": "1.0"},
    ]
    blocks = {("s", 1): {"scope": "s", "module": "m", "lines": [10]}}
    bug = {"module_name": "m", "scope_name": "s", "modify_line": [10]}
    assert find_bug_rank(bug, ranked, blocks) == ("over top-2", "")
