# customize_page.py
import os
import json
import re

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from utils.google_service_helpers import get_sheets_service
from orchestrator_client import create_session, run_orchestrator
from jobs_service.sub_agent.enrichment_agent import enrich_job_search_database

# ---- Config for Job Search sheet ----
JOB_SEARCH_SPREADSHEET_ID = os.environ.get("JOB_SEARCH_SPREADSHEET_ID")
# Tab is 'Sheet1' by default
JOB_SEARCH_RANGE = os.environ.get("JOB_SEARCH_RANGE", "Sheet1!A:Z")

# ---- Prompt template for resume scoring (skills_list will be inserted dynamically) ----
RESUME_SCORE_PROMPT_TEMPLATE = (
    "Go to Google Drive folder (1oWZxO8czQvwjZ-RroN7Lx-jzZxcrzh2Q) and tell me how each of the PDFs "
    "scores on this skills list: you can use google_drive_agent to access that Google Drive location, "
    "but after that you need to use your own reasoning capabilities, and tell me out of 100% what each "
    "resume scores against this list of skills:\n"
    "{skills_list}\n\n"
    "Return your final answer strictly as valid JSON: a list of objects, where each object has the keys "
    "name, id, score, what_is_good, what_is_missing."
)


def top_row(back_label: str = "◀ back", on_back=None, extra=None):
    """Top row with back button and optional extra widget (e.g., job selector)."""
    c1, c2 = st.columns([1, 4])  # second column is wider for dropdown
    with c1:
        if st.button(back_label, use_container_width=True):
            if on_back:
                on_back()
    with c2:
        if extra is not None:
            extra()


def _ensure_session_flags():
    """Initialize session flags used in this page."""
    st.session_state.setdefault("customize_view", "find_jobs")
    st.session_state.setdefault("jobs_search_submitted", False)
    st.session_state.setdefault("jobs_pipeline_started", False)
    st.session_state.setdefault("jobs_pipeline_complete", False)
    st.session_state.setdefault("orchestrator_session_created", False)

    # For customize-resume flow
    st.session_state.setdefault("current_custom_job", None)
    st.session_state.setdefault("needs_resume_scoring", False)
    st.session_state.setdefault("resume_scores", None)
    st.session_state.setdefault("open_resume_index", None)


def _make_buttons_green():
    """Extra CSS to ensure Submit (and other primary) buttons are green."""
    st.markdown(
        """
        <style>
        button[kind="primary"],
        .stForm button[type="submit"] {
            background-color: #22c55e !important;
            color: white !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _fetch_jobs_df() -> pd.DataFrame:
    """Fetch the job search sheet as a pandas DataFrame, padding short rows safely."""
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

        # Pad rows to match header length
        max_len = len(header)
        fixed_rows = [r + [""] * (max_len - len(r)) for r in rows]

        df = pd.DataFrame(fixed_rows, columns=header)
        # Normalize header capitalization
        df.columns = [c.strip() for c in df.columns]
        return df

    except Exception as e:
        st.error(f"Error reading job search sheet: {e}")
        return pd.DataFrame()


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


def _mark_customize_now(selected_row_indices):
    """
    Mark 'customize_now' column as 'yes' for given 0-based DataFrame indices.
    Each index corresponds to sheet row (index + 2), assuming header is row 1.
    """
    if not JOB_SEARCH_SPREADSHEET_ID:
        st.error("JOB_SEARCH_SPREADSHEET_ID is not set in your environment.")
        return

    if not selected_row_indices:
        return

    df = _fetch_jobs_df()
    if df.empty:
        st.error("Cannot update sheet: no data.")
        return

    # Find customize_now column (case-insensitive)
    candidate_cols = [c for c in df.columns if c.strip().lower() == "customize_now"]
    if not candidate_cols:
        st.error("Sheet has no 'customize_now' column. Please add it first.")
        return

    col_name = candidate_cols[0]
    col_idx = list(df.columns).index(col_name)
    col_letter = _col_index_to_letter(col_idx)

    sheet_name = JOB_SEARCH_RANGE.split("!")[0] if "!" in JOB_SEARCH_RANGE else "Sheet1"

    updates = []
    for idx in selected_row_indices:
        rownum = int(idx) + 2  # header is row 1, data starts at row 2
        cell_range = f"{sheet_name}!{col_letter}{rownum}"
        updates.append({"range": cell_range, "values": [["yes"]]})

    try:
        service = get_sheets_service()
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=JOB_SEARCH_SPREADSHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute()
    except Exception as e:
        st.error(f"Failed to update customize_now flags: {e}")


def _render_customize_job_selector():
    """
    Renders a 'Select job' dropdown populated with rows where customize_now == 'yes'.
    - Includes a dummy default option: '-- Select a job --'
    - When a real job is picked, sets flags to trigger resume scoring.
    """
    df = _fetch_jobs_df()
    if df.empty:
        st.write("No jobs found in sheet yet.")
        return

    # Find customize_now column
    cols = [c for c in df.columns if c.strip().lower() == "customize_now"]
    if not cols:
        st.write("No 'customize_now' column in sheet.")
        return
    customize_col = cols[0]

    mask = df[customize_col].astype(str).str.lower().str.strip() == "yes"
    subset = df[mask]
    if subset.empty:
        st.write("No jobs marked for customization yet.")
        return

    if "Jobs" not in subset.columns:
        st.write("No 'Jobs' column in sheet.")
        return

    job_names = subset["Jobs"].tolist()
    # map job name -> original row index (0-based)
    mapping = {job: int(idx) for job, idx in zip(job_names, subset.index)}
    st.session_state["customize_now_job_rows"] = mapping

    dummy_label = "-- Select a job --"
    options = [dummy_label] + job_names

    # Use whatever is currently in the widget state if present, otherwise dummy
    current_label = st.session_state.get("select_job_dropdown", dummy_label)
    if current_label not in options:
        current_label = dummy_label

    selected_label = st.selectbox(
        "Select job",
        options,
        index=options.index(current_label),
        key="select_job_dropdown",
    )

    if selected_label == dummy_label:
        st.session_state["current_custom_job"] = None
    else:
        prev_job = st.session_state.get("current_custom_job")
        if prev_job != selected_label:
            # Job changed → need to rescore resumes
            st.session_state["current_custom_job"] = selected_label
            st.session_state["needs_resume_scoring"] = True
            st.session_state["resume_scores"] = None
            st.session_state["open_resume_index"] = None


def _filter_good_matches(df: pd.DataFrame) -> pd.DataFrame:
    """Return only rows where Good_Match_Yes_No == 'yes' (case-insensitive)."""
    possible_cols = [c for c in df.columns if c.strip().lower() == "good_match_yes_no"]
    if not possible_cols:
        return df.iloc[0:0]
    col = df[possible_cols[0]].astype(str).str.lower().str.strip()
    return df[col == "yes"].copy()


def _run_jobs_pipeline_if_needed():
    """
    If the pipeline was started via the form, run:
      1) Job search pipeline into the sheet (via orchestrator / jobs agent)
      2) Deterministic enrichment of Description + Degree/YOE/Skills
      3) Tag best matches (YOE + location)
    """
    if not st.session_state.get("jobs_pipeline_started", False):
        return
    if st.session_state.get("jobs_pipeline_complete", False):
        return

    # ---- Pull form values from session_state (with sensible defaults) ----
    job_title = st.session_state.get("job_form_job_title", "").strip() or "software engineer"
    company = st.session_state.get("job_form_company", "").strip()
    location = st.session_state.get("job_form_location", "").strip()
    years_exp = st.session_state.get("job_form_years_exp", "").strip()
    days_ago = st.session_state.get("job_form_days_ago", "5 days")  # label from the selectbox

    # Phrase for "posted in the past X"
    days_phrase = f"posted in the past {days_ago}"

    # Optional fragments
    company_clause = f" at {company}" if company else ""
    location_clause = f" in {location}" if location else ""

    with st.spinner("Running job-search agent and enriching your jobs..."):
        if not st.session_state.get("orchestrator_session_created", False):
            create_session(initial_state={"note": "session created from Streamlit"})
            st.session_state["orchestrator_session_created"] = True

        # 1) Scrape / search for jobs --- DYNAMIC via orchestrator
        search_prompt = (
            f"Run the job search pipeline: fetch {job_title} jobs{company_clause}"
            f"{location_clause} {days_phrase} and write them into the Job_Search_Database sheet."
        )
        run_orchestrator(search_prompt)

        # 2) Deterministic enrichment of Description + Degree/YOE/Skills
        try:
            # max_rows=None → all; overwrite=False → only fill empty G/H/I
            enrich_msg = enrich_job_search_database(max_rows=None, overwrite=False)
        except Exception as e:
            st.error(f"Error enriching job descriptions and skills: {e}")

        # 3) Tag best matches --- still via orchestrator + matching agent
        if years_exp and location:
            tag_prompt = (
                f"Tag jobs in the sheet that match {years_exp} experience in {location}, "
                f"including remote jobs that qualify."
            )
        else:
            tag_prompt = (
                "Tag jobs in the sheet that are good matches based on Degree, YOE, and Skills, "
                "including remote jobs that qualify."
            )

        run_orchestrator(tag_prompt)

    st.session_state["jobs_pipeline_complete"] = True


def _render_scraped_jobs_view():
    """Scraped jobs view: shows good matches and lets user pick rows to customize."""
    # --- Local CSS for this view (truncation + column hints + centered checkboxes) ---
    st.markdown(
        """
        <style>
        /* Clamp text to 3 lines with ellipsis */
        .truncate-3 {
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: normal;
        }
        /* Column width hints */
        .jobs-col {
            min-width: 220px;
            max-width: 320px;
        }
        .company-col {
            min-width: 80px;
            max-width: 140px;
        }
        .yoe-col {
            min-width: 50px;
            max-width: 80px;
        }
        .skills-col {
            min-width: 220px;
            max-width: 360px;
        }
        .website-col {
            min-width: 160px;
            max-width: 260px;
        }
        /* Center checkboxes in their column */
        div[data-testid="stCheckbox"] label {
            display: flex;
            align-items: center;
            justify-content: center;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.subheader("Scraped jobs")

    # Run the orchestrator pipeline if needed
    _run_jobs_pipeline_if_needed()

    df = _fetch_jobs_df()
    if df.empty:
        st.info("No scraped jobs found yet. Try submitting a search from 'Find new jobs'.")
        return

    yes_df = _filter_good_matches(df)

    # If still no good matches, show a friendly message
    if yes_df.empty:
        if st.session_state.get("jobs_search_submitted", False):
            st.info(
                "No jobs have been tagged as good matches yet. "
                "Try again in a moment or refine your search in 'Find new jobs'."
            )
        else:
            st.info("No jobs have been marked as good matches yet.")
        return

    # Only show the specific columns the user cares about
    display_cols = ["Jobs", "Website", "Company", "Location", "YOE", "Skills"]
    existing_cols = [c for c in display_cols if c in yes_df.columns]

    # Do NOT reset index: we want original indices to match sheet rows
    data_df = yes_df[existing_cols].copy()

    # Column width ratios (for Streamlit columns)
    base_widths = {
        "Jobs": 2.4,       # wider
        "Website": 2.0,
        "Company": 1.2,    # shorter
        "Location": 1.6,
        "YOE": 0.7,        # very short
        "Skills": 3.0,     # longer
    }
    col_widths = [base_widths.get(c, 1.0) for c in existing_cols] + [0.9]  # last is Customize?

    st.write("")  # small spacer

    # ---- First row: place "Customize selected" button aligned above Customize? column ----
    button_cols = st.columns(col_widths)
    customize_clicked = button_cols[-1].button("Customize selected", use_container_width=True)

    # ---- Header row ----
    header_cols = st.columns(col_widths)
    for i, col_name in enumerate(existing_cols):
        header_cols[i].markdown(f"**{col_name}**")
    header_cols[-1].markdown("**Customize?**")

    # ---- Data rows ----
    for idx, row in data_df.iterrows():
        cols = st.columns(col_widths)

        for j, col_name in enumerate(existing_cols):
            value = str(row[col_name])

            # Column-specific styling + truncation (3 lines), all left-aligned
            if col_name == "Jobs":
                classes = "truncate-3 jobs-col"
                html = f"<div class='{classes}'><strong>{value}</strong></div>"
            elif col_name == "Company":
                classes = "truncate-3 company-col"
                html = f"<div class='{classes}'>{value}</div>"
            elif col_name == "YOE":
                classes = "truncate-3 yoe-col"
                html = f"<div class='{classes}'>{value}</div>"
            elif col_name == "Skills":
                classes = "truncate-3 skills-col"
                html = f"<div class='{classes}'>{value}</div>"
            elif col_name == "Website":
                classes = "truncate-3 website-col"
                url = value.strip()
                if url.startswith("http://") or url.startswith("https://"):
                    html = (
                        f"<div class='{classes}'>"
                        f"<a href='{url}' target='_blank'>{url}</a>"
                        f"</div>"
                    )
                else:
                    html = f"<div class='{classes}'>{value}</div>"
            else:
                classes = "truncate-3"
                html = f"<div class='{classes}'>{value}</div>"

            cols[j].markdown(html, unsafe_allow_html=True)

        checkbox_key = f"scraped_job_customize_{idx}"  # idx is original DF index
        cols[-1].checkbox(" ", key=checkbox_key, label_visibility="collapsed")

    if customize_clicked:
        # Collect original row indices where the box is checked
        selected_indices = [
            idx
            for idx in data_df.index
            if st.session_state.get(f"scraped_job_customize_{idx}", False)
        ]
        if not selected_indices:
            st.warning("No jobs selected yet.")
        else:
            # Mark customize_now="yes" in the sheet for these rows
            _mark_customize_now(selected_indices)
            st.success(f"Selected {len(selected_indices)} jobs for customization.")

            # Remember the selected rows (optional)
            st.session_state["selected_scraped_jobs_indices"] = selected_indices

            # Redirect to Customize resumes tab
            st.session_state["customize_view"] = "customize_resumes"
            st.rerun()


def _get_job_skills(job_name: str) -> str:
    """
    Look up the Skills cell from the sheet for the given job name,
    using the customize_now_job_rows mapping.
    """
    if not job_name:
        return ""
    df = _fetch_jobs_df()
    if df.empty or "Skills" not in df.columns:
        return ""
    mapping = st.session_state.get("customize_now_job_rows", {}) or {}
    row_idx = mapping.get(job_name)
    if row_idx is None:
        return ""
    try:
        value = df.loc[row_idx, "Skills"]
    except Exception:
        return ""
    return str(value).strip()


def _score_resumes_for_current_job():
    """
    Calls the orchestrator to score resumes in the configured Drive folder
    against the Skills list for the currently-selected job.
    Stores results in session_state["resume_scores"].
    """
    job_name = st.session_state.get("current_custom_job")
    skills_list = _get_job_skills(job_name)

    # Fallback: if for some reason the skills cell is empty, still give a sane list
    if not skills_list:
        skills_list = (
            "Statistical Analysis, Probability Theory, Experimental Design, Data Cleaning, "
            "Feature Engineering, Python, R, SQL, Machine Learning, Deep Learning, "
            "Natural Language Processing, Data Visualization, Big Data (Spark, Hadoop), "
            "Cloud Computing (AWS, GCP, Azure), ETL Pipelines, Model Evaluation, "
            "Hyperparameter Tuning, MLOps, Version Control (Git), API Integration"
        )

    prompt = RESUME_SCORE_PROMPT_TEMPLATE.format(skills_list=skills_list)

    events = run_orchestrator(prompt)

    # 1. Find last model event with text
    last_with_text = None
    for ev in reversed(events):
        if ev.get("modelVersion") and ev.get("content"):
            parts = ev["content"].get("parts", [])
            if parts and isinstance(parts[0], dict) and "text" in parts[0]:
                last_with_text = ev
                break

    if last_with_text is None:
        st.error("No model text response found when scoring resumes.")
        return

    raw_text = last_with_text["content"]["parts"][0]["text"]

    # 2. Strip ```json ... ``` fences if they exist
    match = re.search(r"```json\s*(.*?)\s*```", raw_text, re.DOTALL | re.IGNORECASE)
    json_str = match.group(1) if match else raw_text

    # 3. Parse the JSON array of resumes
    try:
        resumes = json.loads(json_str)
        if not isinstance(resumes, list):
            raise ValueError("Parsed JSON is not a list.")
    except Exception as e:
        st.error(f"Failed to parse resume scoring JSON: {e}")
        return

    st.session_state["resume_scores"] = resumes


def _render_customize_resumes_view():
    """Main UI for Customize resumes tab: dropdown already rendered in top_row."""
    st.subheader("Customize resumes")

    job_name = st.session_state.get("current_custom_job")

    # Spinner + orchestrator call when a real job has just been selected
    if st.session_state.get("needs_resume_scoring") and job_name:
        with st.spinner("Analyzing your resumes against this job's skill list..."):
            _score_resumes_for_current_job()
        st.session_state["needs_resume_scoring"] = False

    resumes = st.session_state.get("resume_scores")

    if not job_name:
        st.info("Select a job from the dropdown above to start.")
        return

    if not resumes:
        st.info("Select a job from the dropdown above to score your resumes.")
        return

    # --- Buttons for each resume ---
    st.markdown("#### Pick a resume to review and customize")

    cols = st.columns(len(resumes))
    for i, r in enumerate(resumes):
        name = r.get("name", f"Resume {i+1}")
        score = r.get("score", "N/A")
        label = f"{name}\nScore: {score}%"
        with cols[i]:
            if st.button(label, key=f"resume_btn_{i}"):
                st.session_state["open_resume_index"] = i
                st.rerun()

    # --- "Popup" panel for selected resume ---
    open_idx = st.session_state.get("open_resume_index")
    if open_idx is None:
        return
    if not (0 <= open_idx < len(resumes)):
        return

    selected = resumes[open_idx]
    job_skills = _get_job_skills(job_name)

    st.markdown("---")
    st.markdown(f"### Preview & analysis for `{selected.get('name', '')}`")

    with st.container(border=True):
        top_cols = st.columns([1, 1])
        with top_cols[0]:
            if st.button("Cancel", key="popup_cancel"):
                st.session_state["open_resume_index"] = None
                st.rerun()
        with top_cols[1]:
            if st.button("Customize", key="popup_customize", type="primary"):
                # Placeholder for future customization pipeline
                st.success("Customize action is not implemented yet, but this is where it will run.")

        left_col, right_col = st.columns([2.5, 1.5])
        with left_col:
            file_id = selected.get("id")
            if file_id:
                # Google Drive preview iframe (taller so more of the PDF is visible)
                url = f"https://drive.google.com/file/d/{file_id}/preview"
                components.iframe(url, height=900)
            else:
                st.write("No file ID available for this resume.")

        with right_col:
            # 1) Skills from the sheet for this job (top-right)
            st.markdown("**Target skills for this job (from sheet)**")
            if job_skills:
                st.write(job_skills)
            else:
                st.write("_No skills found in the sheet for this job._")

            # 2) What is good
            st.markdown("**What is good**")
            st.write(selected.get("what_is_good", ""))

            # 3) What is missing
            st.markdown("**What is missing**")
            st.write(selected.get("what_is_missing", ""))


def page_customize():
    _ensure_session_flags()
    _make_buttons_green()

    # ------- Back button logic -------
    def back():
        st.session_state.page = "home"
        st.rerun()

    view = st.session_state.get("customize_view", "find_jobs")

    # Top row: Back + optional job selector
    if view == "customize_resumes":
        top_row(on_back=back, extra=_render_customize_job_selector)
    else:
        top_row(on_back=back)

    # Left panel narrower, main content wider
    left_col, main_col = st.columns([0.8, 3.2])

    # ---- Left navigation buttons ----
    with left_col:
        st.markdown("### ")  # spacer

        if st.button("Find new jobs", use_container_width=True, key="btn_find_jobs"):
            st.session_state["customize_view"] = "find_jobs"
            st.rerun()

        if st.button("Scraped jobs", use_container_width=True, key="btn_scraped_jobs"):
            st.session_state["customize_view"] = "scraped_jobs"
            st.rerun()

        if st.button(
            "Customize resumes",
            use_container_width=True,
            key="btn_customize_resumes",
        ):
            st.session_state["customize_view"] = "customize_resumes"
            st.rerun()

        if st.button(
            "Outreach",
            use_container_width=True,
            key="btn_outreach",
        ):
            st.session_state["customize_view"] = "outreach"
            st.rerun()

    # ---- Main content area ----
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
                    ["1 day", "5 days", "7 days", "14 days", "30 days"],
                )

                submit = st.form_submit_button("Submit", type="primary")

            if submit:
                # Save form inputs into session_state so the pipeline can see them
                st.session_state["job_form_company"] = company.strip()
                st.session_state["job_form_job_title"] = job_title.strip()
                st.session_state["job_form_location"] = location.strip()
                st.session_state["job_form_years_exp"] = years_exp.strip()
                st.session_state["job_form_days_ago"] = days_ago

                # Mark that the user kicked off a search
                st.session_state["jobs_search_submitted"] = True
                st.session_state["jobs_pipeline_started"] = True
                st.session_state["jobs_pipeline_complete"] = False

                # Immediately redirect to Scraped jobs tab
                st.session_state["customize_view"] = "scraped_jobs"
                st.rerun()

        # ======= SCRAPED JOBS VIEW =======
        elif view == "scraped_jobs":
            _render_scraped_jobs_view()

        # ======= CUSTOMIZE RESUMES VIEW =======
        elif view == "customize_resumes":
            _render_customize_resumes_view()

        # ======= OUTREACH VIEW =======
        elif view == "outreach":
            st.subheader("Outreach")
            st.info("Outreach view will go here (to be implemented).")

        else:
            # Fallback: reset to find_jobs
            st.session_state["customize_view"] = "find_jobs"
            st.rerun()
