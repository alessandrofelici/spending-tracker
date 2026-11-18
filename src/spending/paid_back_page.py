"""The Paid back page (/paid-back): purchases friends paid you back for.
paid_back.py draws it."""

import streamlit as st

from spending import bank_sync, paid_back

st.title("Paid back")
plaid = bank_sync.client()
if not isinstance(plaid, str):
    bank_sync.sync_logins(plaid)  # once per visit, whichever page opens first
    paid_back.checking_problem()
paid_back.render()
