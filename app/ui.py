"""Streamlit chat UI for the Property Intelligence Chatbot.

Run: streamlit run app/ui.py
The answer is produced in full by `run_turn` (the finalize node checks citations and numbers before anything
reaches the user), then streamed to the screen word by word. Token-level streaming from the LLM would show
text that the guardrail may still replace, so it is deliberately not used.
"""
import hashlib
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # `streamlit run app/ui.py` from repo root

import streamlit as st  # noqa: E402

from app import config  # noqa: E402
from app.graph import run_turn  # noqa: E402
from app.observability import flush, score_trace  # noqa: E402

EXAMPLES = [
    "Who is DarGlobal and where do they build?",
    "Which DarGlobal projects are in Dubai?",
    "Tell me about the Trump Tower Jeddah project",
    "Show me 3-bedroom apartments for sale on Wasalt",
    "What amenities does Marriott Residences Aida, Oman offer?",
    "What's the weather in Riyadh?",
]

st.set_page_config(page_title="Property Intelligence Chatbot", page_icon="🏙️", layout="centered")

ss = st.session_state
ss.setdefault("session_id", uuid.uuid4().hex)
ss.setdefault("messages", [])  # {"role", "content", "sources", "trace_id", "cost", "feedback"}
ss.setdefault("pending", None)

with st.sidebar:
    st.header("Settings")
    mode = st.radio("Retrieval mode", ["hybrid", "semantic", "keyword"], index=0,
                    help="Hybrid = dense embeddings + BM25 sparse, fused with RRF.")
    show_cost = st.toggle("Show cost and latency per answer", value=False)
    if st.button("New conversation", use_container_width=True):
        ss.messages, ss.pending, ss.session_id = [], None, uuid.uuid4().hex
        st.rerun()
    st.caption("Answers come only from scraped DarGlobal project/company pages and a Wasalt (KSA) listings "
               "sample. Not investment advice.")

st.title("🏙️ Property Intelligence Chatbot")
st.caption("Ask about DarGlobal projects, the company, its news, or Wasalt listings in Saudi Arabia.")


def _client_key() -> str | None:
    """Salted hash of the client IP (from Render's proxy header) used only for rate limiting and as the
    anonymous Langfuse user id. The raw IP is never stored or sent anywhere."""
    try:
        ip = (st.context.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or st.context.ip_address
    except Exception:  # noqa: BLE001
        ip = None
    if not isinstance(ip, str) or not ip:
        return None
    salt = os.getenv("INDEX_SIGNING_KEY", "") + "|client"
    return hashlib.sha256((salt + ip).encode()).hexdigest()[:16]


_MD_SPECIAL = set(chr(92) + "`*_{}[]()#+!<>|~-")  # markdown control characters (chr(92) = backslash)


def _md_escape(text: str) -> str:
    return "".join(chr(92) + c if c in _MD_SPECIAL else c for c in text)


def _stream(text: str):
    for word in text.split(" "):
        yield word + " "
        time.sleep(0.012)


def _render_extras(i: int, msg: dict) -> None:
    if msg.get("sources"):
        with st.expander(f"Sources ({len(msg['sources'])})"):
            for s in msg["sources"]:
                st.markdown(f"- [{_md_escape(str(s.get('title') or s['url'])[:120])}]({s['url']})")
    if show_cost and msg.get("cost"):
        c = msg["cost"]
        eq = c.get("equivalent_cost_usd")
        st.caption(f"{c.get('llm_calls', 0)} LLM calls · {c.get('prompt_tokens', 0)}+{c.get('completion_tokens', 0)} "
                   f"tokens · ${c.get('actual_cost_usd', 0):.4f} actual"
                   + (f" (${eq:.4f} at paid price)" if eq else "")
                   + f" · {c.get('latency_ms', {}).get('total', 0) / 1000:.1f}s")
    if msg.get("trace_id"):
        if msg.get("feedback") is None:
            fb = st.feedback("thumbs", key=f"fb_{i}")
            if fb is not None:
                msg["feedback"] = fb
                score_trace(msg["trace_id"], "user_feedback", float(fb))
                st.toast("Thanks for the feedback!")
        else:
            st.caption("👍 Thanks!" if msg["feedback"] == 1 else "👎 Thanks, noted.")


for i, m in enumerate(ss.messages):
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] == "assistant":
            _render_extras(i, m)

if not ss.messages:
    st.write("**Try one of these:**")
    cols = st.columns(2)
    for j, q in enumerate(EXAMPLES):
        if cols[j % 2].button(q, key=f"ex_{j}", use_container_width=True):
            ss.pending = q
            st.rerun()

prompt = st.chat_input("Ask about a project, a city, or listings...") or ss.pending
if prompt:
    ss.pending = None
    history = [{"role": m["role"], "content": m["content"]} for m in ss.messages]
    ss.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Searching projects and listings..."):
            try:
                r = run_turn(prompt, history=history, session_id=ss.session_id, user_id=_client_key(), mode=mode)
            except Exception:  # noqa: BLE001 - never show a stack trace to the user; server log only
                logging.getLogger("ui").exception("run_turn failed")
                r = {"answer": "Sorry, something went wrong on my side. Please try again in a moment."}
        st.write_stream(_stream(r["answer"]))
    flush()
    ss.messages.append({"role": "assistant", "content": r["answer"], "sources": r.get("sources", []),
                        "trace_id": r.get("trace_id"), "cost": r.get("cost"), "feedback": None})
    st.rerun()  # re-render so sources and feedback widgets appear under the answer

st.divider()
st.caption(f"Data as of {config.SNAPSHOT_DATE} · sources: darglobal.co.uk, wasalt.sa (sitemap sample)")
