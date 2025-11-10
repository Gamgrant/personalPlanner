# ui/chat_page.py
import os

import pandas as pd
import streamlit as st
import time
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

def _col_index_to_letter(idx: int) -> str:
    """Convert 0-based column index to Excel-style letter (0 -> A, 25 -> Z, 26 -> AA)."""
    idx = int(idx)
    letters = ""
    while True:
        idx, rem = divmod(idx, 26)
        letters = chr(ord("A") + rem) + letters
        if idx == 0:
            break
    return letters


def _ensure_fallback_phone_for_rows(row_indices):
    """
    For the given 0-based DataFrame row indices, if 'Outreach Phone Number'
    is blank/whitespace AND the row has a non-empty 'resume_id_latex_done',
    fill it with the fallback '6082074227'.
    """
    if not JOB_SEARCH_SPREADSHEET_ID:
        return

    df = _fetch_jobs_df()
    if df.empty:
        return

    # Find Outreach Phone Number column (case-insensitive)
    phone_cols = [c for c in df.columns if c.strip().lower() == "outreach phone number"]
    if not phone_cols:
        return
    phone_col = phone_cols[0]

    # Find resume_id_latex_done column (case-insensitive)
    resume_cols = [c for c in df.columns if c.strip().lower() == "resume_id_latex_done"]
    resume_col = resume_cols[0] if resume_cols else None

    col_idx = list(df.columns).index(phone_col)
    col_letter = _col_index_to_letter(col_idx)
    sheet_name = JOB_SEARCH_RANGE.split("!")[0] if "!" in JOB_SEARCH_RANGE else "Sheet1"

    updates = []
    for idx in row_indices:
        try:
            row = df.iloc[idx]
        except Exception:
            continue

        # If we can see resume_id_latex_done and it's empty → skip this row entirely
        if resume_col is not None:
            if not str(row[resume_col]).strip():
                continue

        current_val = str(row[phone_col]).strip()
        if current_val:
            # Already has a phone number → leave it as-is
            continue

        rownum = int(idx) + 2  # header is row 1 → df index 0 → row 2
        updates.append(
            {
                "range": f"{sheet_name}!{col_letter}{rownum}",
                "values": [["6082074227"]],
            }
        )

    if not updates:
        return

    try:
        service = get_sheets_service()
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=JOB_SEARCH_SPREADSHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute()
    except Exception as e:
        st.error(f"Failed to enforce fallback phone number: {e}")

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

        # Build a human-readable description of ONLY the selected rows
        selected_rows_desc_lines = []
        for idx in selected_indices:
            row = df.loc[idx]
            rownum = int(idx) + 2  # sheet row number
            job = str(row.get("Jobs", "")).strip()
            company = str(row.get("Company", "")).strip()
            location = str(row.get("Location", "")).strip()
            selected_rows_desc_lines.append(
                f"- Sheet row {rownum}: Job='{job}', Company='{company}', Location='{location}'"
            )
                # 1) Build a description of the selected sheet rows for the LLM
        #    (these are 0-based indices in df, but sheet rows are 1-based with header = 1)
        selected_rows_desc = ", ".join(
            f"sheet row {idx + 2}" for idx in selected_indices
        )

        # 2) Single prompt: run full Apollo pipeline (recruiters + scripts + Gmail drafts)
        prompt_outreach_pipeline = (
            "You are manager_apollo_agent running the recruiter outreach pipeline over the "
            "Job_Search_Database sheet in the spreadsheet whose ID is JOB_SEARCH_SPREADSHEET_ID.\n\n"
            "The user has explicitly selected the following data rows (1-based, header is row 1):\n"
            f"{selected_rows_desc}\n\n"
            "You MUST obey all of the following rules:\n"
            "1) ROW SCOPE\n"
            "   - Only touch the listed rows above. Do NOT modify any other rows in the sheet.\n"
            "   - For each listed row, you MUST first check the column 'resume_id_latex_done'.\n"
            "   - If 'resume_id_latex_done' is empty or missing for that row, SKIP that row entirely\n"
            "     and leave all outreach-related columns unchanged.\n\n"
            "2) OUTREACH COLUMNS TO FILL (for eligible rows only)\n"
            "   For each selected row where 'resume_id_latex_done' is non-empty:\n"
            "   - Use apollo_outreach_agent to find appropriate recruiter contacts and write:\n"
            "       • Outreach Name\n"
            "       • Outreach email\n"
            "       • Outreach Phone Number\n"
            "     into the Job_Search_Database sheet for that row.\n"
            "   - Do not overwrite a valid existing Outreach email unless clearly necessary.\n\n"
            "3) PHONE FALLBACK RULE (CRITICAL)\n"
            "   - For each selected row with non-empty 'resume_id_latex_done':\n"
            "       • If you cannot find a specific recruiter phone number from Apollo or anywhere else,\n"
            "         you MUST still fill the Outreach Phone Number cell with this exact value: 6082074227.\n"
            "       • Never leave Outreach Phone Number blank for those selected rows that have a\n"
            "         non-empty 'resume_id_latex_done'.\n\n"
            "4) OUTREACH EMAIL SCRIPT (script_agent)\n"
            "   - After enriching recruiters, call script_agent to generate a personalized outreach email\n"
            "     for each selected row where:\n"
            "       • 'resume_id_latex_done' is non-empty,\n"
            "       • Outreach Name is present,\n"
            "       • Outreach email is present,\n"
            "       • Outreach Email Script is currently empty.\n"
            "   - Write the email text ONLY into the 'Outreach Email Script' column for that row.\n"
            "   - Do not overwrite any existing Outreach Email Script.\n\n"
            "5) GMAIL DRAFTS (gmail_outreach_agent)\n"
            "   - Without asking the user any questions, call gmail_outreach_agent to create Gmail drafts\n"
            "     based on the 'Outreach Email Script' and 'Outreach email' and 'resume_id_latex_done'\n"
            "     for the selected rows.\n"
            "   - Drafts ONLY (never send emails).\n"
            "   - Attach the resume from 'resume_id_latex_done' to each draft.\n"
            "   - Ensure at most one draft per unique recruiter email address.\n\n"
            "6) INTERACTION\n"
            "   - Do NOT ask the user for confirmation or additional input.\n"
            "   - Just run the entire pipeline (Apollo enrichment → scripts → Gmail drafts) for the\n"
            "     eligible selected rows and then stop.\n"
            "   - Return a brief, factual status summary: how many rows were enriched, how many got\n"
            "     Outreach Email Scripts, and how many Gmail drafts were created.\n"
        )

        with st.spinner(
            "Running outreach pipeline: finding recruiters, writing scripts, and creating email drafts..."
        ):
            try:
                # Single call: manager_apollo_agent / apollo_pipeline handles everything.
                run_orchestrator(prompt_outreach_pipeline)

                # Safety net: still enforce fallback phone locally for the selected rows
                # in case any row slipped through without a phone value.
                _ensure_fallback_phone_for_rows(selected_indices)
            except Exception as e:
                st.error(f"Error while running outreach orchestrator: {e}")
                return


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
      - PLUS interactive checkbox column: "Call Right now?" and a green submit button
        that triggers the orchestrator.
    """
    selected_indices = st.session_state.get("outreach_selected_indices", [])
    if not selected_indices:
        st.session_state["outreach_mode"] = "select"
        st.session_state["outreach_selected_indices"] = []
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

    # New displayed recruiter columns
    new_cols = [
        "Recruiter Name",
        "Recruiter's email",
        "Recruiter's phone number",
    ]

    # Extra interactive column for this view
    CALL_COL = "Call Right now?"

    # All columns to show in the details table
    all_cols = existing_cols + new_cols + [CALL_COL]

    st.markdown("#### Selected jobs for outreach")

    # Back button to go back to selection view
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
        "Call Right now?": 1.2,
    }
    col_widths = [base_widths.get(c, 1.0) for c in all_cols]

    # Header
    header_cols = st.columns(col_widths)
    for i, col_name in enumerate(all_cols):
        header_cols[i].markdown(f"**{col_name}**")

    # CSS for truncation
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

    # Rows
    for idx, row in subset.iterrows():
        cols = st.columns(col_widths)
        for j, col_name in enumerate(all_cols):
            if col_name == CALL_COL:
                # Interactive checkbox for "Call Right now?"
                checkbox_key = f"call_now_{idx}"
                cols[j].checkbox(
                    "",
                    key=checkbox_key,
                    label_visibility="collapsed",
                )
            else:
                value = str(row.get(col_name, ""))
                html = f"<div class='truncate-2'>{value}</div>"
                cols[j].markdown(html, unsafe_allow_html=True)

    st.write("")

    # Green submit button (uses type='primary'; you already style primary as green)
    if st.button("Submit 'Call Right now' selection", type="primary", key="submit_call_now"):
        # Collect which recruiters were marked "Call Right now?"
        selected_to_call = [
            idx
            for idx in subset.index
            if st.session_state.get(f"call_now_{idx}", False)
        ]

        if not selected_to_call:
            st.warning("Please select at least one recruiter to call.")
            return

        # Build sheet-row description for orchestrator (1-based rows, header = row 1)
        selected_rows_desc = ", ".join(
            f"sheet row {int(idx) + 2}" for idx in selected_to_call
        )

        # 👉 TODO: you will replace this with your real instructions
        prompt_call_pipeline = (
            "You are the agent that handles 'Call Right now' actions for selected recruiters "
            "from the Job_Search_Database Google Sheet.\n\n"
            f"The user has selected the following sheet rows (1-based, header row is 1):\n"
            f"{selected_rows_desc}\n\n"
            ">>> INSERT DETAILED CALL INSTRUCTIONS HERE <<<\n"
        )

        with st.spinner("Triggering call workflow via orchestrator..."):
            try:
                run_orchestrator(prompt_call_pipeline)
                # You can optionally show a success summary here once your orchestrator responds
            except Exception as e:
                st.error(f"Error while running 'Call Right now' orchestrator: {e}")


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
