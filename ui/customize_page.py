# customize_page.py
import streamlit as st


def top_row(back_label="◀ back", on_back=None):
    c1, _ = st.columns([1, 5])
    with c1:
        if st.button(back_label, use_container_width=True):
            if on_back:
                on_back()


def page_customize():
    # ------- Back button logic -------
    def back():
        st.session_state.page = "home"
        st.rerun()

    top_row(on_back=back)

    # ------- Left panel state -------
    if "customize_view" not in st.session_state:
        st.session_state["customize_view"] = "find_jobs"  # default

    left_col, main_col = st.columns([1, 3])

    with left_col:
        st.markdown("### ")  # small spacer at top

        # Left panel buttons
        if st.button("Find new jobs", use_container_width=True, key="btn_find_jobs"):
            st.session_state["customize_view"] = "find_jobs"
            st.rerun()

        if st.button("Customize resumes", use_container_width=True, key="btn_customize_resumes"):
            st.session_state["customize_view"] = "customize_resumes"
            st.rerun()

    with main_col:
        view = st.session_state.get("customize_view", "find_jobs")

        # ======= FIND NEW JOBS VIEW =======
        if view == "find_jobs":
            st.subheader("Find new jobs")

            with st.form("find_jobs_form"):
                company = st.text_input("What company are you interested in? (if any)")
                job_title = st.text_input("What jobs you are looking to find?")
                location = st.text_input("Location preference")
                years_exp = st.text_input("Years of Experience")
                days_ago = st.selectbox(
                    "Days Ago posted",
                    ["1 day", "5 days", "1 week", "2 weeks", "1 month"],
                )

                submit = st.form_submit_button("Submit", type="primary")

            if submit:
                # TODO: hook into your actual job-search logic
                st.success(
                    f"Searching for '{job_title}'"
                    f"{' at ' + company if company else ''}, "
                    f"location: '{location}', "
                    f"{years_exp} years experience, "
                    f"posted within {days_ago}."
                )

        # ======= CUSTOMIZE RESUMES VIEW =======
        else:
            st.subheader("Customize resumes")
            st.info("Resume customization view will go here (to be implemented).")
