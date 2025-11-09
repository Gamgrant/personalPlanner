# customize_page.py
import streamlit as st

def top_row(back_label="◀ back", on_back=None):
    c1, _ = st.columns([1, 5])
    with c1:
        if st.button(back_label, use_container_width=True):
            if on_back:
                on_back()

def page_customize():
    def back():
        st.session_state.page = "home"
        st.rerun()

    top_row(on_back=back)
    st.subheader("Customize resumes & Outreach for jobs")
    st.info("Placeholder page. Wire in your logic later.")
