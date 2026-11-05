import streamlit as st

st.set_page_config(page_title="SkySnap", page_icon=":partly_sunny:")

st.navigation(
    [
        st.Page("ask_page.py", title="Ask SkySnap", default=True),
        st.Page("dashboard_page.py", title="Dashboard", url_path="dashboard"),
    ]
).run()
