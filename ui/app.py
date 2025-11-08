# app.py
import streamlit as st
import re
import streamlit.components.v1 as components
   # <-- add this import

st.set_page_config(page_title="Simple Career Helper", page_icon="💼", layout="wide")

# ---------- Hardcoded login (username + password only) ----------
USE_LOGIN = False  # set True later to re-enable the username/password screen
ALLOWED_USERNAME = "steven"
ALLOWED_PASSWORD = "letmein"   # change for your demo

# ---------- Session defaults ----------
def ss_defaults():
    st.session_state.setdefault("authed", not USE_LOGIN)
    st.session_state.setdefault("page", "home")   # start on home, not "login"
    st.session_state.setdefault("resumes", [])
    st.session_state.setdefault("projects", [])
    st.session_state.setdefault("links", [])
    st.session_state.setdefault("tmp_links", [""])
ss_defaults()


# ---------- Helpers ----------
def is_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s.strip(), flags=re.I))

def grid_of_files(items: list):
    cols = st.columns(3)
    for i, item in enumerate(items):
        with cols[i % 3]:
            with st.container(border=True):
                st.write(f"**{item['name']}**")
                st.download_button(
                    "View / Download",
                    data=item["bytes"],
                    file_name=item["name"],
                    use_container_width=True,
                )

def top_row(back_label="◀ back", on_back=None, right_button_label=None, on_right=None):
    c1, _, c3 = st.columns([1, 5, 1])
    with c1:
        if st.button(back_label, use_container_width=True):
            if on_back: on_back()
    with c3:
        if right_button_label and st.button(right_button_label, use_container_width=True):
            if on_right: on_right()

# ---------- PAGES ----------
# def page_login():
#     st.title("🔒 Login")
#     st.caption("Demo auth: hardcoded username + password.")

#     with st.form("login_form"):
#         username = st.text_input("Username")
#         password = st.text_input("Password", type="password")
#         ok = st.form_submit_button("Login")

#     if ok:
#         if username.strip().lower() == ALLOWED_USERNAME and password == ALLOWED_PASSWORD:
#             st.session_state.authed = True
#             st.session_state.page = "home"
#             st.success("Welcome!")
#             st.rerun()
#         else:
#             st.error("Invalid username or password (demo creds are hardcoded).")

def page_home():
    # Read nav set by the anchor click and route
    nav = st.query_params.get("nav", None)
    if nav in {"upload", "customize", "chat"}:
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
            width:100%; min-height:40vh;                 /* BIG cards */
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
        st.markdown('<a class="bigbtn" href="?nav=chat" target="_self">'
                    'Chat — Ask me anything</a>', unsafe_allow_html=True)



def page_upload():
    def back():
        st.session_state.page = "home"; st.rerun()
    def open_add_docs():
        st.session_state.page = "add_docs"
        st.session_state.tmp_links = [""]
        st.rerun()

    top_row(on_back=back, right_button_label="➕ Add documents", on_right=open_add_docs)

    st.subheader("Resumes")
    if st.session_state.resumes:
        grid_of_files(st.session_state.resumes)
    else:
        st.info("No resumes yet. Click **Add documents** to upload.")

    st.markdown("## Projects")
    if st.session_state.projects:
        grid_of_files(st.session_state.projects)
    else:
        st.info("No project files yet. Click **Add documents** to upload.")

    st.markdown("## Provided links")
    if st.session_state.links:
        for url in st.session_state.links:
            st.markdown(f"- [{url}]({url})")
    else:
        st.info("No links yet.")

def page_add_docs():
    def cancel_and_back():
        st.session_state.page = "upload"; st.rerun()
    def add_link_field():
        st.session_state.tmp_links.append(""); st.rerun()

    def submit():
        proj_files = st.session_state.get("_proj_files", [])
        res_files  = st.session_state.get("_res_files", [])

        for f in proj_files or []:
            st.session_state.projects.append({"name": f.name, "bytes": f.getvalue()})
        for f in res_files or []:
            st.session_state.resumes.append({"name": f.name, "bytes": f.getvalue()})

        cleaned = [s.strip() for s in st.session_state.tmp_links if s.strip() and is_url(s)]
        st.session_state.links.extend(cleaned)

        st.session_state.page = "upload"; st.rerun()

    with st.container(border=True):
        c1, _, c3 = st.columns([1, 5, 1])
        with c1:
            st.markdown(
                '<span style="background:#ffa74d;padding:8px 14px;border-radius:8px;color:black;font-weight:600;">Back</span>',
                unsafe_allow_html=True,
            )
            if st.button("←", help="Back (discard changes)"): cancel_and_back()
        with c3:
            st.button("Submit", type="primary", use_container_width=True, on_click=submit)

        st.markdown("### Please drop **projects** here")
        st.file_uploader("Projects", accept_multiple_files=True, key="_proj_files", label_visibility="collapsed")

        st.markdown("### Please drop your **resumes** here")
        st.file_uploader("Resumes", accept_multiple_files=True, key="_res_files", label_visibility="collapsed")

        st.markdown("### Add links")
        for i, _ in enumerate(st.session_state.tmp_links):
            st.session_state.tmp_links[i] = st.text_input(
                f"Link {i+1}",
                value=st.session_state.tmp_links[i],
                placeholder="https://example.com/portfolio",
                key=f"link_{i}",
            )
        st.button("➕ Add another link", on_click=add_link_field)

def page_customize():
    def back(): st.session_state.page = "home"; st.rerun()
    top_row(on_back=back)
    st.subheader("Customize resumes & Outreach for jobs")
    st.info("Placeholder page. Wire in your logic later.")

def page_chat():
    def back(): st.session_state.page = "home"; st.rerun()
    top_row(on_back=back)
    st.subheader("Chat — Ask me anything")
    st.info("Placeholder chat page.")

# ---------- Router ----------
if USE_LOGIN and not st.session_state.authed:
    st.write("Login is disabled in this build. Set USE_LOGIN=True to enable.")
else:
    page = st.session_state.page
    if page == "home": page_home()
    elif page == "upload": page_upload()
    elif page == "add_docs": page_add_docs()
    elif page == "customize": page_customize()
    elif page == "chat": page_chat()
    else:
        st.session_state.page = "home"; st.rerun()
