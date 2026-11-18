"""The dashboard's entry point (`uv run spend dashboard`): its pages."""

import streamlit as st

st.set_page_config(page_title="Spending", layout="wide")
st.navigation(
    [
        st.Page(
            "dashboard.py", title="Spending", icon=":material/bar_chart:", default=True
        ),
        st.Page(
            "paid_back_page.py",
            title="Paid back",
            icon=":material/payments:",
            url_path="paid-back",
        ),
        st.Page(
            "settings_page.py",
            title="Connected accounts",
            icon=":material/account_balance:",
            url_path="settings",
        ),
    ],
    position="top",
).run()
