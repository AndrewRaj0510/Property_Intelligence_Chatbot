"""Streamlit chat UI for the Property Intelligence Chatbot.

Run: streamlit run app/ui.py
The answer is produced in full by `run_turn` (the finalize node checks citations and numbers before anything
reaches the user), then streamed to the screen word by word. Token-level streaming from the LLM would show
text that the guardrail may still replace, so it is deliberately not used.
"""
import hashlib
import html
import logging
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # `streamlit run app/ui.py` from repo root

import streamlit as st  # noqa: E402

from app import config  # noqa: E402
from app.observability import flush, score_trace  # noqa: E402

EXAMPLES = [
    "Who is DarGlobal and where do they build?",
    "Which DarGlobal projects are in Dubai?",
    "Tell me about the Trump Tower Jeddah project",
    "Show me 3-bedroom apartments for sale on Wasalt",
    "What amenities does Marriott Residences Aida, Oman offer?",
    "What's the weather in Riyadh?",
]

@st.cache_resource(show_spinner=False)
def _agent_loader() -> dict:
    """Import the agent stack (LangGraph, OpenAI, Qdrant, Langfuse: several seconds on Render's 0.1 CPU) in a
    background thread, once per process, so the page renders immediately instead of after the imports."""
    box: dict = {"ready": threading.Event()}

    def load() -> None:
        try:
            from app.graph import run_turn
            box["run_turn"] = run_turn
        except Exception:  # noqa: BLE001
            logging.getLogger("ui").exception("agent import failed")
        finally:
            box["ready"].set()
    threading.Thread(target=load, daemon=True, name="agent-warmup").start()
    return box


def run_turn(*args, **kwargs) -> dict:
    box = _agent_loader()
    box["ready"].wait(timeout=180)
    if "run_turn" not in box:
        raise RuntimeError("agent not loaded")
    return box["run_turn"](*args, **kwargs)


_agent_loader()  # start warming as soon as the first visitor opens the page

st.set_page_config(page_title="Property Intelligence Chatbot", page_icon="🏙️", layout="centered")

# Hover card for per-answer cost/latency (pure CSS: no click, no JS). Opens on hover and on keyboard focus.
st.markdown("""<style>
.cost-badge{position:relative;display:inline-block;margin:.25rem 0 .1rem}
.cost-badge .pill{display:inline-flex;align-items:center;gap:.35rem;padding:.15rem .6rem;border:1px solid rgba(128,128,128,.35);
  border-radius:999px;font-size:.78rem;opacity:.8;cursor:default;user-select:none}
.cost-badge .pill .arrow{font-size:.65rem;transition:transform .15s}
.cost-badge:hover .pill,.cost-badge:focus-within .pill{opacity:1;border-color:#b08d57}
.cost-badge:hover .arrow,.cost-badge:focus-within .arrow{transform:rotate(180deg)}
.cost-badge .card{visibility:hidden;opacity:0;transition:opacity .12s;position:absolute;left:0;top:calc(100% + 6px);z-index:1000;
  min-width:300px;padding:.6rem .8rem;border-radius:10px;font-size:.8rem;line-height:1.5;
  background:#fffdf8;color:#31333f;border:1px solid #b08d57;box-shadow:0 6px 20px rgba(0,0,0,.18)}
.cost-badge:hover .card,.cost-badge:focus-within .card{visibility:visible;opacity:1}
.cost-badge .card b{font-weight:600;color:#7a5c2e}.cost-badge .card .muted{color:#6b6f7b}
</style>""", unsafe_allow_html=True)


def _cost_badge_html(c: dict, lat: dict, eq) -> str:
    """Badge + hover card. Only numbers we formatted ourselves go in; labels are escaped anyway."""
    esc = html.escape
    total = float(lat.get("total", 0) or 0) / 1000
    steps = " · ".join(f"{esc(str(k))} {float(v) / 1000:.2f} s" for k, v in lat.items() if k != "total")
    tokens = (f"{int(c.get('prompt_tokens', 0) or 0):,} in / {int(c.get('completion_tokens', 0) or 0):,} out"
              f" · {int(c.get('llm_calls', 0) or 0)} LLM call(s)")
    if c.get("embedding_tokens"):
        tokens += f" · {int(c['embedding_tokens']):,} embedding tokens"
    cost = f"&#36;{float(c.get('actual_cost_usd', 0) or 0):.4f} actual (free models)"
    if eq:
        cost += f"<br><span class='muted'>&#36;{float(eq):.4f} at the paid price of the same model</span>"
    return (f"<div class='cost-badge' tabindex='0' aria-label='Cost and latency details'>"
            f"<span class='pill'>⏱ {total:.1f}s · cost <span class='arrow'>▼</span></span>"
            f"<div class='card' role='tooltip'><b>Latency:</b> {total:.2f} s total"
            + (f"<br><span class='muted'>{steps}</span>" if steps else "")
            + f"<br><b>Tokens:</b> {tokens}<br><b>Cost:</b> {cost}</div></div>")

ss = st.session_state
ss.setdefault("session_id", uuid.uuid4().hex)
ss.setdefault("messages", [])  # {"role", "content", "sources", "trace_id", "cost", "feedback"}
ss.setdefault("pending", None)

st.title("🏙️ Property Intelligence Chatbot")
st.caption("Ask about DarGlobal projects, the company, its news, or Wasalt listings in Saudi Arabia.")
if ss.messages and st.button("🔄 New conversation"):
    ss.messages, ss.pending, ss.session_id = [], None, uuid.uuid4().hex
    st.rerun()


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
        time.sleep(0.003)  # cosmetic typing effect only; keep it short


def _render_extras(i: int, msg: dict) -> None:
    if msg.get("sources"):
        with st.expander(f"Sources ({len(msg['sources'])})"):
            for s in msg["sources"]:
                st.markdown(f"- [{_md_escape(str(s.get('title') or s['url'])[:120])}]({s['url']})")
    if msg.get("cost"):
        c = msg["cost"]
        lat = c.get("latency_ms", {}) or {}
        eq = c.get("equivalent_cost_usd")
        st.markdown(_cost_badge_html(c, lat, eq), unsafe_allow_html=True)
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
                r = run_turn(prompt, history=history, session_id=ss.session_id, user_id=_client_key())
            except Exception:  # noqa: BLE001 - never show a stack trace to the user; server log only
                logging.getLogger("ui").exception("run_turn failed")
                r = {"answer": "Sorry, something went wrong on my side. Please try again in a moment."}
        st.write_stream(_stream(r["answer"]))
    flush()
    ss.messages.append({"role": "assistant", "content": r["answer"], "sources": r.get("sources", []),
                        "trace_id": r.get("trace_id"), "cost": r.get("cost"), "feedback": None})
    st.rerun()  # re-render so sources and feedback widgets appear under the answer

st.divider()
st.caption("Answers come only from scraped DarGlobal project/company pages and a Wasalt (KSA) listings "
           "sample. Not investment advice.")
st.caption(f"Data as of {config.SNAPSHOT_DATE} · sources: darglobal.co.uk, wasalt.sa (sitemap sample)")
