# ui/chat_page.py
import os

import pandas as pd
import streamlit as st

from utils.google_service_helpers import get_sheets_service
from orchestrator_client import run_orchestrator  # <-- NEW


# ---- Config for Job Search sheet ----
JOB_SEARCH_SPREADSHEET_ID = os.environ.get("JOB_SEARCH_SPREADSHEET_ID")
JOB_SEARCH_RANGE = os.environ.get("JOB_SEARCH_RANGE", "Sheet1!A:Z")


def top_row(back_label="◀ back", on_back=None):
    c1, _ = st.columns([1, 5])
    with c1:
        if st.button(back_label, use_container_width=True):
            if on_back:
                on_back()


def _fetch_jobs_df() -> pd.DataFrame:
    """Fetch the job search sheet as a pandas DataFrame."""
    if not JOB_SEARCH_SPREADSHEET_ID:
        st.error("JOB_SEARCH_SPREADSHEET_ID is not set in your environment.")
        return pd.DataFrame()

    try:
        sheets_service = get_sheets_service()
        result = (
            sheets_service.spreadsheets()
            .values()
            .get(spreadsheetId=JOB_SEARCH_SPREADSHEET_ID, range=JOB_SEARCH_RANGE)
            .execute()
        )
        values = result.get("values", [])
        if not values:
            return pd.DataFrame()

        header, *rows = values

        # Pad rows so every row has len(header) columns
        max_len = len(header)
        fixed_rows = [r + [""] * (max_len - len(r)) for r in rows]

        df = pd.DataFrame(fixed_rows, columns=[c.strip() for c in header])
        return df
    except Exception as e:
        st.error(f"Error reading job search sheet: {e}")
        return pd.DataFrame()


def _filter_with_resume_done(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep only rows where resume_id_latex_done is non-empty and non-whitespace.
    """
    if df.empty:
        return df

    cols = [c for c in df.columns if c.strip().lower() == "resume_id_latex_done"]
    if not cols:
        # No such column → nothing qualifies
        return df.iloc[0:0]

    col_name = cols[0]
    mask = df[col_name].astype(str).str.strip() != ""
    return df[mask].copy()


def _ensure_outreach_state():
    st.session_state.setdefault("outreach_mode", "select")  # "select" or "details"
    st.session_state.setdefault("outreach_selected_indices", [])


def _render_outreach_selection(df: pd.DataFrame, existing_cols):
    """
    First view:
      - Show Jobs, Company, Location, Degree, YOE, Skills
      - Plus interactive checkbox column:
        'Find contact info of recruiter and reach out'
    """
    st.markdown(
        """
        <style>
        .truncate-2 {
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: normal;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("#### Select jobs you want to find recruiter contact info for")

    # Widths for visible columns + checkbox column
    base_widths = {
        "Jobs": 2.4,
        "Company": 1.6,
        "Location": 1.6,
        "Degree": 1.0,
        "YOE": 0.8,
        "Skills": 3.0,
    }
    col_widths = [base_widths.get(c, 1.0) for c in existing_cols] + [1.8]

    # Header row
    header_cols = st.columns(col_widths)
    for i, col_name in enumerate(existing_cols):
        header_cols[i].markdown(f"**{col_name}**")
    header_cols[-1].markdown("**Find contact info of recruiter and reach out**")

    # Data rows with checkboxes
    for idx, row in df.iterrows():
        cols = st.columns(col_widths)

        for j, col_name in enumerate(existing_cols):
            value = str(row[col_name])
            html = f"<div class='truncate-2'>{value}</div>"
            cols[j].markdown(html, unsafe_allow_html=True)

        checkbox_key = f"outreach_select_{idx}"
        cols[-1].checkbox(
            "",
            key=checkbox_key,
            label_visibility="collapsed",
        )

    st.write("")
    if st.button("Submit selection", type="primary", key="submit_outreach_selection"):
        # Collect selected indices
        selected_indices = [
            idx
            for idx in df.index
            if st.session_state.get(f"outreach_select_{idx}", False)
        ]
        if not selected_indices:
            st.warning("Please select at least one job before submitting.")
            return

        # ---- NEW: call orchestrator under spinner ----
        prompt = (
            "Can you look for recruiters based on the Job_Search_Database on the customized resume. "
            "Fill out the Outreach Email, Outreach Phone Number, and Draft Email for the Recruiters"
        )

        with st.spinner("Finding recruiters and populating outreach info via orchestrator..."):
            try:
                run_orchestrator(prompt)
            except Exception as e:
                st.error(f"Error while running outreach orchestrator: {e}")

        # Save selection + switch mode
        st.session_state["outreach_selected_indices"] = selected_indices
        st.session_state["outreach_mode"] = "details"
        st.rerun()


def _render_outreach_details(df: pd.DataFrame, existing_cols):
    """
    Second view:
      - Keep only selected rows.
      - Show Jobs, Company, Location, Degree, YOE, Skills
      - PLUS 3 new columns: Recruiter Name, Recruiter's email, Recruiter's phone number
        populated from sheet columns:
          Outreach Name, Outreach email, Outreach Phone Number.
    """
    selected_indices = st.session_state.get("outreach_selected_indices", [])
    if not selected_indices:
        st.session_state["outreach_mode"] = "select"
        st.rerun()
        return

    # Rebuild subset from freshly fetched DF
    subset = df.loc[selected_indices].copy()

    # Map sheet outreach columns → display recruiter columns
    outreach_to_recruiter = {
        "Outreach Name": "Recruiter Name",
        "Outreach email": "Recruiter's email",
        "Outreach Phone Number": "Recruiter's phone number",
    }
    for outreach_col, recruiter_col in outreach_to_recruiter.items():
        if outreach_col in subset.columns:
            subset[recruiter_col] = subset[outreach_col].astype(str)
        else:
            subset[recruiter_col] = ""

    new_cols = [
        "Recruiter Name",
        "Recruiter's email",
        "Recruiter's phone number",
    ]
    all_cols = existing_cols + new_cols

    st.markdown("#### Selected jobs for outreach")

    if st.button("⬅ Start over", key="outreach_back_to_select"):
        st.session_state["outreach_mode"] = "select"
        st.session_state["outreach_selected_indices"] = []
        st.rerun()

    # Column widths
    base_widths = {
        "Jobs": 2.4,
        "Company": 1.6,
        "Location": 1.6,
        "Degree": 1.0,
        "YOE": 0.8,
        "Skills": 3.0,
        "Recruiter Name": 2.0,
        "Recruiter's email": 2.2,
        "Recruiter's phone number": 2.0,
    }
    col_widths = [base_widths.get(c, 1.0) for c in all_cols]

    # Header
    header_cols = st.columns(col_widths)
    for i, col_name in enumerate(all_cols):
        header_cols[i].markdown(f"**{col_name}**")

    # Rows (no more interactive checkbox column)
    for idx, row in subset.iterrows():
        cols = st.columns(col_widths)
        for j, col_name in enumerate(all_cols):
            value = str(row.get(col_name, ""))
            html = f"<div class='truncate-2'>{value}</div>"
            cols[j].markdown(html, unsafe_allow_html=True)


def page_outreach():
    """
    Outreach - call recruiters and send them custom email.

    Every time this view is opened:
      * Fetch the latest Job Search sheet.
      * Filter to rows where 'resume_id_latex_done' is non-empty.
      * Mode 'select': show Jobs, Company, Location, Degree, YOE, Skills + checkbox column.
      * On submit:
          - call orchestrator with outreach prompt under a spinner
          - then keep only selected rows and show:
            Recruiter Name, Recruiter's email, Recruiter's phone number
            populated from sheet columns:
              Outreach Name, Outreach email, Outreach Phone Number.
    """
    def back():
        st.session_state.page = "home"
        st.rerun()

    top_row(on_back=back)
    st.subheader("Outreach - call recruiters and send them custom email")

    _ensure_outreach_state()

    # Always fetch fresh data when page is opened/rerun
    df = _fetch_jobs_df()
    df = _filter_with_resume_done(df)

    if df.empty:
        st.info(
            "No jobs with a completed customized resume yet. "
            "Once 'resume_id_latex_done' is filled in the sheet, they will show up here."
        )
        return

    # Only show these base columns from sheet
    base_cols = ["Jobs", "Company", "Location", "Degree", "YOE", "Skills"]
    existing_cols = [c for c in base_cols if c in df.columns]

    outreach_mode = st.session_state.get("outreach_mode", "select")

    if outreach_mode == "select":
        _render_outreach_selection(df, existing_cols)
    else:
        _render_outreach_details(df, existing_cols)
