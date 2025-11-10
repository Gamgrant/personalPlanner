# app.py
import sys
from pathlib import Path

import streamlit as st

# ---- Make sure Python can see the project root (personalPlanner) ----
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

# Import page functions from other modules
from upload_page import page_upload, page_add_docs
from customize_page import page_customize
from ui.outreach_page import page_outreach

# ---------- Hardcoded login (username + password only) ----------
USE_LOGIN = False  # set True later to re-enable the username/password screen
ALLOWED_USERNAME = "steven"
ALLOWED_PASSWORD = "letmein"   # change for your demo

st.set_page_config(page_title="Simple Career Helper", page_icon="💼", layout="wide")

# Global styling: primary buttons green
st.markdown("""
    <style>
    /* Make primary buttons green */
    div.stButton > button[kind="primary"] {
        background-color: #22c55e;
        color: white;
    }
    </style>
""", unsafe_allow_html=True)


# ---------- Session defaults ----------
def ss_defaults():
    st.session_state.setdefault("authed", not USE_LOGIN)
    st.session_state.setdefault("page", "home")   # start on home

    # UI source-of-truth for what's shown in Upload view
    st.session_state.setdefault("resumes", [])   # list of {"id", "name", "bytes"}
    st.session_state.setdefault("projects", [])
    st.session_state.setdefault("links", [])     # list of strings

    # temp link inputs in Add Documents page
    st.session_state.setdefault("tmp_links", [""])

    # Delete-mode state for upload page
    st.session_state.setdefault("delete_mode", False)
    st.session_state.setdefault("delete_resumes", set())
    st.session_state.setdefault("delete_projects", set())
    st.session_state.setdefault("delete_links", set())

ss_defaults()


# ---------- Home Page ----------
def page_home():
    # Read nav set by the anchor click and route
    nav = st.query_params.get("nav", None)
    if nav in {"upload", "customize", "outreach"}:
        st.query_params.clear()
        st.session_state.page = nav
        st.rerun()

    # Styles
    st.markdown("""
        <style>
        #home-title {
            text-align:center; font-size:32px; font-weight:700;
            margin-top:1.4rem; margin-bottom:3.5rem;
        }
        .bigbtn {
            display:flex; align-items:center; justify-content:center;
            width:100%; min-height:40vh;
            border-radius:22px; border:1px solid rgba(255,255,255,.15);
            background: rgba(255,255,255,.06);
            text-decoration:none !important;
            color:#fff !important; font-weight:700; font-size:20px;
            line-height:1.35; text-align:center; padding:0 18px;
        }
        .bigbtn:hover { filter:brightness(1.12); }
        @media (max-width:900px){
            .bigbtn { min-height:45vh; font-size:18px; }
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown('<div id="home-title">👋 Hey Steven, what do you want to do today?</div>', unsafe_allow_html=True)

    # Anchor cards (stay in same tab via target="_self")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown('<a class="bigbtn" href="?nav=upload" target="_self">'
                    'Upload/view documents</a>', unsafe_allow_html=True)
    with c2:
        st.markdown('<a class="bigbtn" href="?nav=customize" target="_self">'
                    'Customize resumes & Outreach for jobs</a>', unsafe_allow_html=True)
    with c3:
        st.markdown('<a class="bigbtn" href="?nav=outreach" target="_self">'
                    'Outreach - call recruiters and send them custom email</a>', unsafe_allow_html=True)


# ---------- Router ----------
if USE_LOGIN and not st.session_state.authed:
    st.write("Login is disabled in this build. Set USE_LOGIN=True to enable.")
else:
    page = st.session_state.page
    if page == "home":
        page_home()
    elif page == "upload":
        page_upload()
    elif page == "add_docs":
        page_add_docs()
    elif page == "customize":
        page_customize()
    elif page == "outreach":
        page_outreach()
    else:
        st.session_state.page = "home"
        st.rerun()
