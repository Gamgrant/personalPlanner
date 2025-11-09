# chat_page.py
import streamlit as st

def top_row(back_label="◀ back", on_back=None):
    c1, _ = st.columns([1, 5])
    with c1:
        if st.button(back_label, use_container_width=True):
            if on_back:
                on_back()

def page_chat():
    def back():
        st.session_state.page = "home"
        st.rerun()

    top_row(on_back=back)
    st.subheader("Chat — Ask me anything")
    st.info("Placeholder chat page.")
