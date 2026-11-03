"""Ask SkySnap: natural-language questions over the weather warehouse."""

import os

import pandas as pd
import streamlit as st

from skysnap_ask.db import QueryError

CITIES = "Bengaluru, Mumbai, Delhi, Chennai, Kolkata"
RANGE_SQL = "SELECT MIN(DATE(observed_at)), MAX(DATE(observed_at)) FROM weather_hourly"
SAMPLES = [
    "Which city had the most rain between 2026-10-01 and 2026-10-07?",
    "What was the average temperature in Mumbai yesterday?",
    "Which city is the windiest on average?",
]

st.set_page_config(page_title="Ask SkySnap", page_icon=":partly_sunny:")
st.title("Ask SkySnap")


@st.cache_data(ttl=600)
def date_range(_service) -> str | None:
    result = _service.runner(RANGE_SQL)
    low, high = result.rows[0]
    if low is None or high is None:
        return None
    return f"{low} to {high}"


def caption(service) -> str:
    text = f"Weather for {CITIES}."
    try:
        span = date_range(service)
    except QueryError:
        span = None
    return f"{text} Data available: {span}." if span else text


def pick(question: str) -> None:
    st.session_state["question"] = question
    st.session_state["pending"] = question


def submit() -> None:
    st.session_state["pending"] = st.session_state.get("question", "")


def show_result(result) -> None:
    if result.status == "answered":
        st.success(result.answer)
        if result.sql:
            st.code(result.sql, language="sql")
        st.dataframe(pd.DataFrame(result.rows, columns=result.columns))
    elif result.status == "refused":
        st.info(result.answer)
    else:
        st.error(result.error)
        if result.sql:
            st.code(result.sql, language="sql")
    if result.context:
        with st.expander("Retrieved context"):
            for chunk in result.context:
                st.markdown(f"**{chunk.id}**\n\n{chunk.text}")


if not os.environ.get("GEMINI_API_KEY"):
    st.warning(
        "Ask SkySnap needs a Gemini API key. Set `GEMINI_API_KEY` (plus `GEMINI_MODEL` and "
        "`GEMINI_EMBED_MODEL`) in your `.env` file, then restart with "
        "`docker compose up -d ask`."
    )
    st.stop()

if "service" not in st.session_state:
    from skysnap_ask.config import load_settings
    from skysnap_ask.factory import build_service

    with st.spinner("Preparing the knowledge index..."):
        st.session_state["service"] = build_service(load_settings())
service = st.session_state["service"]

st.caption(caption(service))

st.write("Try one of these:")
for i, sample in enumerate(SAMPLES):
    st.button(sample, key=f"sample_{i}", on_click=pick, args=(sample,))

st.text_input("Your question", key="question")
st.button("Ask", key="ask", on_click=submit)

pending = st.session_state.pop("pending", None)
if pending is not None:
    with st.spinner("Thinking..."):
        st.session_state["result"] = service.ask(pending)

if "result" in st.session_state:
    show_result(st.session_state["result"])
