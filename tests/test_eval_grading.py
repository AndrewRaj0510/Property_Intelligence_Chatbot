"""Offline tests for the eval grader (no network). Run: python tests/test_eval_grading.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.e2e_eval import grade, pct  # noqa: E402

U = "https://darglobal.co.uk/oman/azure"


def r(answer, sources=(), tools=(), **kw):
    return {"answer": answer, "sources": list(sources), "tools_used": list(tools), "guardrail": {}, "error": None, **kw}


def test_correct_with_valid_citation():
    g = grade({"must_contain_any": ["Azure"], "expect_tools": ["search_projects"]},
              r(f"Azure Oceanfront Villas ({U}).", [U], ["search_projects"]))
    assert g["correct"] and g["tool_ok"] and g["citation_present"] and g["citation_valid"]


def test_invented_citation_is_invalid():
    g = grade({"must_contain_any": ["Azure"]}, r("Azure, see https://evil.example/x", [U]))
    assert g["citation_present"] and not g["citation_valid"]


def test_must_contain_all():
    assert not grade({"must_contain": ["48", "villa"]}, r("There are 48 apartments."))["correct"]


def test_refusal_and_block():
    assert grade({"expect_refusal": True}, r("That is outside my data; please check an official source."))["correct"]
    assert grade({"expect_refusal": True}, r("blocked", blocked=True))["correct"]
    assert not grade({"expect_refusal": True}, r("Rates are 5.5% today."))["correct"]


def test_provider_failure_excluded():
    g = grade({"must_contain": ["x"]}, r("The free AI model is busy right now (rate limit)."))
    assert g["provider_fail"] and "correct" not in g


def test_leak_fails_answer_even_if_facts_right():
    g = grade({"must_contain_any": ["DG1"]}, r("DG1 is great. Key: sk-or-v1-0123456789abcdef0123"))
    assert not g["leak_free"] and not g["correct"] and "secret" in g["leaks"]


def test_refusal_that_leaks_code_is_wrong():
    g = grade({"expect_refusal": True}, r("I can't help, but app/tools.py has it."))
    assert g["refusal_ok"] and not g["correct"]


def test_must_not_contain():
    g = grade({"must_contain_any": ["DG1"], "must_not_contain": ["chunk_id"]}, r("DG1 chunk_id=abc"))
    assert not g["correct"] and g["forbidden_hits"] == ["chunk_id"]


def test_summary_rejects_free_text():
    from eval.summary import _clean
    try:
        _clean("the answer was: call +966 5555 5555")
        raise AssertionError("free text accepted")
    except ValueError:
        pass
    assert _clean(0.95) == 0.95 and _clean("baseline") == "baseline"


def test_pct():
    assert pct([1, 2, 3, 4], 50) == 2 and pct([1, 2, 3, 4], 95) == 4 and pct([], 50) == 0.0


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)}/{len(tests)} passed")
