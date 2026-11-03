"""Bounded brain: protocol, budget, feedback. No network."""

from types import SimpleNamespace

from squad.brain import run_goal


def _model(*replies):
    it = iter(replies)

    class M:
        def invoke(self, transcript):
            return SimpleNamespace(content=next(it))
    return M()


REG = {"graph_summary": {
    "description": "d",
    "func": lambda: {"ok": True, "revision": 9}}}


def test_tool_then_answer():
    out = run_goal(
        "report rev", REG,
        _model('Prose {"tool": "graph_summary", "args": {}} prose',
               '{"answer": "rev 9"}'))
    assert out == {"ok": True, "answer": "rev 9", "tool_calls": 1,
                   "iterations": 2}


def test_budget_exhausted():
    out = run_goal(
        "never ends", REG,
        _model(*['{"tool": "graph_summary", "args": {}}'] * 5),
        max_iterations=2)
    assert out["ok"] is False
    assert out["reason"] == "iteration budget exhausted"
    assert (out["tool_calls"], out["iterations"]) == (2, 2)


def test_unknown_tool_fed_back_without_counting():
    out = run_goal(
        "typo", REG,
        _model('{"tool": "nope", "args": {}}', '{"answer": "done"}'))
    assert out["ok"] is True and out["answer"] == "done"
    assert out["tool_calls"] == 0 and out["iterations"] == 2


def test_bad_json_reprompted():
    out = run_goal("speak", REG, _model("hello", '{"answer": "hi"}'))
    assert out["ok"] is True and out["answer"] == "hi"
    assert out["tool_calls"] == 0


def test_tool_error_returned_not_raised():
    def bad():
        raise RuntimeError("down")
    reg = {"t": {"description": "d", "func": bad}}
    out = run_goal("x", reg,
                   _model('{"tool": "t", "args": {}}',
                          '{"answer": "reported"}'),
                   max_iterations=3)
    assert out["ok"] is True and out["tool_calls"] == 1


def test_model_error_ends_run():
    class M:
        def invoke(self, transcript):
            raise RuntimeError("down")
    out = run_goal("x", REG, M())
    assert out["ok"] is False and "model error" in out["reason"]
