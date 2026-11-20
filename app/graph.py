"""LangGraph agent: agent -> tools loop (max 3 tool rounds) -> finalize (deterministic checks).

Conversation memory: the caller passes previous turns (`history`, OpenAI-format user/assistant messages);
the last `HISTORY_TURNS` turns are sent to the model. Each turn is one Langfuse trace with child
observations per LLM call and tool call, plus token, cost and latency totals (per-query cost capture).
"""
import json
import operator
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app import config, embeddings, guardrail, llm, security, tools
from app.observability import flush, observation, score, trace_attributes
from app.prompts import SYSTEM_PROMPT

NOT_FOUND = "I couldn't find that in the DarGlobal / Wasalt data I have."
ERROR_REPLY = "Sorry, something went wrong on my side. Please try again in a moment."
RATE_LIMITED_REPLY = "You're sending messages very quickly. Please wait a minute and try again."
MODES = {"hybrid", "semantic", "keyword"}
_limiter = security.RateLimiter(config.RATE_LIMIT_TURNS, config.RATE_LIMIT_WINDOW_S)
_global_limiter = security.RateLimiter(config.RATE_LIMIT_GLOBAL_TURNS, 60)  # all users: protects the shared quota


class ChatState(TypedDict, total=False):
    question: str
    history: list[dict]
    mode: str                                         # retrieval mode: hybrid | semantic | keyword
    messages: Annotated[list[dict], operator.add]     # this turn: user, assistant, tool messages
    tool_rounds: int
    sources: Annotated[list[dict], operator.add]
    llm_calls: Annotated[list[dict], operator.add]
    tool_calls: Annotated[list[dict], operator.add]
    answer: str
    guardrail: dict
    error: str


def _trim_history(history: list[dict]) -> list[dict]:
    # History is untrusted client state: only user/assistant text (no old tool results: biggest token saving),
    # control chars stripped, PII redacted, long messages truncated; user turns that would be blocked now are
    # dropped together with their reply, so a refused injection can't come back through memory.
    msgs: list[dict] = []
    skip_reply = False
    for m in history:
        role, content = m.get("role"), m.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        if role == "user":
            skip_reply = guardrail.check_input(content)[1] is not None
            if skip_reply:
                continue
        elif skip_reply:
            skip_reply = False
            continue
        text = security.redact_pii(security.strip_control(content))[:config.HISTORY_MSG_CHARS]
        msgs.append({"role": role, "content": text})
    user_idx = [i for i, m in enumerate(msgs) if m["role"] == "user"]
    if len(user_idx) > config.HISTORY_TURNS:
        msgs = msgs[user_idx[-config.HISTORY_TURNS]:]
    return msgs


# ------------------------------------------------------------------ nodes
def agent_node(state: ChatState) -> dict:
    rounds = state.get("tool_rounds", 0)
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, *_trim_history(state.get("history", [])),
            *state.get("messages", [])]
    final_round = rounds >= config.MAX_TOOL_ROUNDS
    with observation("llm", as_type="generation", input=msgs[-3:],
                     metadata={"tool_round": rounds, "forced_answer": final_round}) as gen:
        try:
            res = llm.chat(msgs, tools=tools.openai_schemas(), tool_choice="none" if final_round else "auto")
        except llm.LLMBusyError as e:
            gen.update(level="ERROR", status_message="all models busy")
            return {"messages": [{"role": "assistant", "content": str(e)}], "error": "busy"}
        gen.update(model=res.model, output=res.message,
                   usage_details={"input": res.prompt_tokens, "output": res.completion_tokens},
                   cost_details={"total": res.cost_usd},
                   metadata={"equivalent_cost_usd": res.equivalent_cost_usd, "latency_ms": round(res.latency_ms)})
    msg = res.message
    if final_round:
        msg.pop("tool_calls", None)
    call = {"model": res.model, "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens,
            "cost_usd": res.cost_usd, "equivalent_cost_usd": res.equivalent_cost_usd, "latency_ms": res.latency_ms,
            "cached_tokens": int((res.raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)}
    return {"messages": [msg], "llm_calls": [call]}


def tools_node(state: ChatState) -> dict:
    last = state["messages"][-1]
    mode = state.get("mode", "hybrid")
    out_msgs, sources, calls = [], [], []
    for tc in last.get("tool_calls", []):
        name, args = tc["function"]["name"], tc["function"]["arguments"]
        t0 = time.perf_counter()
        with observation(name, as_type="tool", input=args) as span:
            res = tools.run_tool(name, args, mode=mode)
            span.update(output=res.text[:4000], metadata={"timings_ms": res.timings_ms, **res.meta})
        calls.append({"name": name, "args": args, "latency_ms": (time.perf_counter() - t0) * 1000,
                      "timings_ms": res.timings_ms, "meta": res.meta})
        sources += res.sources
        content, n_inj = guardrail.sanitize_tool_text(name, res.text, config.TOOL_OUTPUT_CHARS)
        calls[-1]["injection_neutralised"] = n_inj
        out_msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": content})
    return {"messages": out_msgs, "sources": sources, "tool_calls": calls,
            "tool_rounds": state.get("tool_rounds", 0) + 1}


def finalize_node(state: ChatState) -> dict:
    draft = (state["messages"][-1].get("content") or "").strip()
    if state.get("error") == "busy":
        return {"answer": draft, "guardrail": {}}
    sources = state.get("sources", [])
    urls = [s["url"] for s in sources if security.is_allowed_url(s.get("url", ""))]  # only allowlisted links citable
    report: dict[str, Any] = {"unsupported_numbers": [], "unknown_urls": [], "sources_appended": False,
                              "replaced": False}
    with observation("finalize", as_type="guardrail", input=draft[:2000]) as g:
        if not draft:
            draft, report["replaced"] = NOT_FOUND, True
        bad = guardrail.unsupported_numbers(draft, [s["text"] for s in sources], state["question"])
        if bad:
            report["unsupported_numbers"] = bad
            # Drop only the sentences/bullets with an unsupported number; keep the rest if it still says something.
            trimmed = guardrail.drop_unsupported(draft, [s["text"] for s in sources], state["question"])
            if trimmed is not None:
                draft, report["trimmed"] = trimmed, True
            else:
                report["replaced"] = True
                first = next(iter(urls), None)
                draft = ("That information isn't published in the data I have"
                         + (f". You can check the live page: {first}" if first else " from DarGlobal or Wasalt."))
        if guardrail.leaks_system_prompt(draft, SYSTEM_PROMPT):
            report["prompt_leak"], report["replaced"] = True, True
            draft = guardrail.INJECTION_REPLY
        leaks = security.find_leaks(draft)
        if {"secret", "code", "internal"} & set(leaks):  # never show keys, source code or index internals
            report["output_leak"], report["replaced"] = leaks, True
            draft = guardrail.INJECTION_REPLY
        elif "pii" in leaks:  # personal contact details: redact, keep the rest of the answer
            report["pii_redacted"] = True
            draft = security.redact_pii(draft)
        draft, n_markup = guardrail.strip_active_markup(draft)
        if n_markup:
            report["active_markup_removed"] = n_markup
        draft, report["unknown_urls"] = guardrail.strip_unknown_urls(draft, set(urls))
        if urls and not guardrail.cited_urls(draft) and not report["replaced"]:
            uniq = list(dict.fromkeys(urls))[:5]
            draft += "\n\nSources:\n" + "\n".join(f"- {u}" for u in uniq)
            report["sources_appended"] = True
        g.update(output=draft[:2000], metadata=report)
        score("unsupported_number", 1.0 if bad else 0.0)
        score("unknown_url", float(len(report["unknown_urls"])))
    return {"answer": draft, "guardrail": report}


def _route(state: ChatState) -> str:
    last = state["messages"][-1]
    if last.get("tool_calls") and state.get("tool_rounds", 0) < config.MAX_TOOL_ROUNDS:
        return "tools"
    return "finalize"


def build_graph():
    g = StateGraph(ChatState)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node)
    g.add_node("finalize", finalize_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", _route, {"tools": "tools", "finalize": "finalize"})
    g.add_edge("tools", "agent")
    g.add_edge("finalize", END)
    return g.compile()


GRAPH = build_graph()


# ------------------------------------------------------------------ entry point
def _reply(answer: str, t0: float, **extra) -> dict:
    return {"answer": answer, "sources": [], "tools_used": [], "guardrail": {}, "trace_id": None, "error": None,
            "cost": {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "actual_cost_usd": 0.0,
                     "latency_ms": {"total": round((time.perf_counter() - t0) * 1000)}}, **extra}


def run_turn(question: str, history: list[dict] | None = None, session_id: str | None = None,
             user_id: str | None = None, mode: str = "hybrid") -> dict:
    """Answer one user turn. Returns answer, sources, tools used, and the per-query cost/latency record.
    Never raises: every failure becomes a generic message (details only in Langfuse, no stack trace to user)."""
    t0 = time.perf_counter()
    mode = mode if mode in MODES else "hybrid"
    if (session_id and not _limiter.allow(session_id)) or (user_id and not _limiter.allow("ip:" + user_id)):
        return _reply(RATE_LIMITED_REPLY, t0, error="rate_limited")
    if not _global_limiter.allow("global"):
        return _reply(RATE_LIMITED_REPLY, t0, error="rate_limited_global")
    question, refusal = guardrail.check_input(question)
    # PII the user typed (their email / phone) never leaves the process: not to the LLM, not to Langfuse.
    question = security.redact_pii(question)
    attrs = dict(session_id=session_id, user_id=user_id, trace_name="chat_turn")
    if refusal:  # blocked before any LLM call: zero tokens spent
        with trace_attributes(**attrs, tags=["blocked_input"]):
            with observation("input_guardrail", as_type="guardrail", input=question[:500], output=refusal):
                score("input_blocked", 1.0)
        return _reply(refusal, t0, guardrail={"input_blocked": True}, blocked=True)

    emb_turn: dict = {}
    embeddings.TURN_USAGE.set(emb_turn)
    state: ChatState = {"question": question, "history": _trim_history(history or []),
                        "mode": mode, "messages": [{"role": "user", "content": question}], "tool_rounds": 0}
    with trace_attributes(**attrs, tags=[f"mode:{mode}"]):
        with observation("chat_turn", as_type="agent", input={"question": question}) as root:
            try:
                final = GRAPH.invoke(state, {"recursion_limit": 4 * config.MAX_TOOL_ROUNDS + 4})
            except Exception as e:  # noqa: BLE001
                root.update(level="ERROR", status_message=type(e).__name__)
                return _reply(ERROR_REPLY, t0, error="internal")
            cost = _cost_record(final, emb_turn, (time.perf_counter() - t0) * 1000)
            tools_used = sorted({c["name"] for c in final.get("tool_calls", [])})
            srcs = list({s["url"]: {"url": s["url"], "title": s.get("title", "")}
                         for s in final.get("sources", []) if security.is_allowed_url(s.get("url", ""))}.values())
            root.update(output={"answer": final.get("answer", "")},
                        metadata={"tools_used": tools_used, "tool_rounds": final.get("tool_rounds", 0),
                                  "models": sorted({c["model"] for c in final.get("llm_calls", [])}),
                                  "guardrail": final.get("guardrail"), "cost": cost, "retrieval_mode": mode})
            trace_id = getattr(root, "trace_id", None)
    return {"answer": final.get("answer", ""), "sources": srcs, "tools_used": tools_used,
            "guardrail": final.get("guardrail", {}), "cost": cost, "trace_id": trace_id,
            "error": final.get("error")}


def _cost_record(final: dict, emb_turn: dict, total_ms: float) -> dict:
    calls = final.get("llm_calls", [])
    pt = sum(c["prompt_tokens"] for c in calls)
    ct = sum(c["completion_tokens"] for c in calls)
    eq = [c["equivalent_cost_usd"] for c in calls]
    emb_tokens = emb_turn.get("tokens", 0)
    step: dict[str, float] = {"llm": sum(c["latency_ms"] for c in calls)}
    for tc in final.get("tool_calls", []):
        for k, v in tc["timings_ms"].items():
            if k in ("embed", "qdrant", "sqlite", "expand"):
                step[k] = step.get(k, 0.0) + v
    return {
        "llm_calls": len(calls), "prompt_tokens": pt, "completion_tokens": ct,
        "cached_prompt_tokens": sum(c.get("cached_tokens", 0) for c in calls),
        "embedding_calls": emb_turn.get("calls", 0), "embedding_tokens": emb_tokens,
        "actual_cost_usd": round(sum(c["cost_usd"] for c in calls), 6),
        "equivalent_cost_usd": round(sum(e for e in eq if e), 6) if any(eq) else None,
        "latency_ms": {k: round(v) for k, v in step.items()} | {"total": round(total_ms)},
        "models": [c["model"] for c in calls],
    }


if __name__ == "__main__":
    import sys
    r = run_turn(" ".join(sys.argv[1:]) or "Who is DarGlobal?")
    print(r["answer"])
    print(json.dumps({k: r[k] for k in ("tools_used", "guardrail", "cost")}, indent=2))
    flush()
