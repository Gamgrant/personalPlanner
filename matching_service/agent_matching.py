import os
import re
from typing import List

import pandas as pd

from google.adk.agents import Agent
from google.genai import types

from utils.google_service_helpers import (
    get_sheets_service as _get_sheets_service,
)

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")

# ======================================================
# Service wrapper
# ======================================================

def get_sheets_service():
    """
    Thin wrapper around utils.google_service_helpers.get_sheets_service().
    Uses centralized OAuth/token handling.
    """
    return _get_sheets_service()

# ======================================================
# Helpers
# ======================================================

def _col_index_to_letter(idx: int) -> str:
    """
    Convert 0-based column index to Excel-style column letter.
    0 -> A, 25 -> Z, 26 -> AA, etc.
    """
    idx += 1  # switch to 1-based
    letters: List[str] = []
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters.append(chr(ord("A") + rem))
    return "".join(reversed(letters))


def normalize_location(loc):
    if pd.isna(loc):
        return None
    loc = str(loc).lower().strip()
    if "remote" in loc:
        return "remote"
    # Take first token before comma/slash/pipe/hyphen
    loc = re.split(r"[,/|\-]", loc)[0].strip()
    return loc


def _load_sheet_as_df(sheet_name: str) -> tuple[pd.DataFrame, list[str]]:
    """
    Load an entire sheet into a DataFrame.

    Returns:
        df: DataFrame for all data rows (no header row).
        headers: list of column names as read from row 1.
    """
    sheets = get_sheets_service()
    spreadsheet_id = os.environ.get("JOB_SEARCH_SPREADSHEET_ID")
    if not spreadsheet_id:
        raise ValueError("JOB_SEARCH_SPREADSHEET_ID is not set in the environment.")

    # Read a wide range; adjust if needed
    result = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A1:Z10000")
        .execute()
    )
    values = result.get("values", [])
    if not values:
        raise ValueError(f"No data found in sheet '{sheet_name}'.")

    headers = values[0]
    data_rows = values[1:]

    # Pad / trim rows to header length for clean DataFrame construction
    normalized_rows = []
    for row in data_rows:
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
        else:
            row = row[: len(headers)]
        normalized_rows.append(row)

    df = pd.DataFrame(normalized_rows, columns=headers)
    return df, headers


def _write_good_match_column(
    sheet_name: str,
    headers: list[str],
    df: pd.DataFrame,
    rows_to_mark_yes: list[int],
) -> str:
    """
    Update the Good_Match_Yes_No column for the given sheet.
    rows_to_mark_yes are DataFrame indices (0-based, data-only rows).

    We only touch that single column.
    """
    sheets = get_sheets_service()
    spreadsheet_id = os.environ.get("JOB_SEARCH_SPREADSHEET_ID")
    if not spreadsheet_id:
        raise ValueError("JOB_SEARCH_SPREADSHEET_ID is not set in the environment.")

    # Find or create the Good_Match_Yes_No column
    col_name = "Good_Match_Yes_No"
    if col_name not in headers:
        headers.append(col_name)
        # existing df does NOT yet have the column; create it
        df[col_name] = ""

    col_idx = headers.index(col_name)

    # Pull existing column values so we don't clobber them
    # (header row + data rows)
    existing_col_values = [col_name]
    if col_name in df.columns:
        existing_col_values += df[col_name].astype(str).tolist()
    else:
        existing_col_values += ["" for _ in range(len(df))]

    # Mark matches as "yes" (keep existing values otherwise)
    for idx in rows_to_mark_yes:
        # idx is 0-based data row, so +1 for header
        existing_col_values[idx + 1] = "yes"

    col_letter = _col_index_to_letter(col_idx)
    end_row = len(existing_col_values)
    update_range = f"{sheet_name}!{col_letter}1:{col_letter}{end_row}"

    body = {
        "values": [[v] for v in existing_col_values]
    }

    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=update_range,
        valueInputOption="RAW",
        body=body,
    ).execute()

    return (
        f"Updated column '{col_name}' in sheet '{sheet_name}'. "
        f"Marked 'yes' for {len(rows_to_mark_yes)} rows."
    )

# ======================================================
# Main tool: mark good matches
# ======================================================

def mark_good_matches_for_yoe_and_location(
    sheet_name: str = "Sheet1", 
    target_yoe: str = "5 years",
    target_location: str = "san francisco",
) -> str:
    """
    Read the job search sheet, normalize Location + YOE, expand remote rows,
    and mark Good_Match_Yes_No='yes' for all original rows that match
    (target_yoe, target_location).

    Args:
        sheet_name: Name of the sheet/tab within the spreadsheet
                    referenced by JOB_SEARCH_SPREADSHEET_ID.
        target_yoe: Normalized YOE string (e.g., '5 years').
        target_location: Normalized location string (e.g., 'san francisco').

    Returns:
        A summary string.
    """
    # 1. Load sheet
    df, headers = _load_sheet_as_df(sheet_name)

    # We need these columns; adjust names here if your sheet uses different ones
    if "Location" not in df.columns or "YOE" not in df.columns:
        missing = [c for c in ["Location", "YOE"] if c not in df.columns]
        raise ValueError(
            f"Missing expected column(s) in sheet '{sheet_name}': {missing}. "
            "Make sure your headers contain 'Location' and 'YOE'."
        )

    # Track original row index (0-based for data rows).
    df = df.copy()
    df["row_idx"] = range(len(df))

    # -------------------------------
    # 2. Normalize location
    # -------------------------------
    df["Location_norm"] = df["Location"].apply(normalize_location)

    # -------------------------------
    # 3. Normalize YOE
    #    - "5+ years" -> "5 years"
    #    - "5 Years" -> "5 years"
    # -------------------------------
    df["YOE_norm"] = (
        df["YOE"]
        .astype(str)
        .str.lower()
        .str.replace("+", "", regex=False)
        .str.strip()
    )

    # -------------------------------
    # 4. Expand remote rows
    # -------------------------------
    expanded_rows = []
    unique_locs = df["Location_norm"].dropna().unique()

    for _, row in df.iterrows():
        if row["Location_norm"] == "remote":
            # replicate this row for every non-remote location
            for loc in unique_locs:
                if loc != "remote":
                    new_row = row.copy()
                    new_row["Location_norm"] = loc
                    expanded_rows.append(new_row)
        else:
            expanded_rows.append(row)

    df_expanded = pd.DataFrame(expanded_rows)

    # -------------------------------
    # 5. Group & count combinations
    # -------------------------------
    group_summary = (
        df_expanded
        .groupby(["YOE_norm", "Location_norm"], dropna=False)
        .size()
        .reset_index(name="Count")
        .sort_values(by="Count", ascending=False)
    )

    # -------------------------------
    # 6. Get the specific combo
    # -------------------------------
    match = group_summary[
        (group_summary["YOE_norm"] == target_yoe)
        & (group_summary["Location_norm"] == target_location)
    ]

    if match.empty:
        return (
            f"No rows found for combination "
            f"(YOE='{target_yoe}', Location='{target_location}') "
            f"in sheet '{sheet_name}'. No updates made."
        )

    subset = df_expanded[
        (df_expanded["YOE_norm"] == target_yoe)
        & (df_expanded["Location_norm"] == target_location)
    ]

    # Collect original row indices (from the base df) that participated.
    # row_idx is 0-based with respect to data rows (excluding header).
    rows_to_mark_yes = sorted(set(int(idx) for idx in subset["row_idx"].tolist()))

    # Write back to Good_Match_Yes_No column
    msg = _write_good_match_column(
        sheet_name=sheet_name,
        headers=headers,
        df=df,
        rows_to_mark_yes=rows_to_mark_yes,
    )

    # Add some debug info
    summary = (
        f"{msg} "
        f"Target combo: (YOE='{target_yoe}', Location='{target_location}'). "
        f"Total matching expanded rows: {len(subset)}. "
        f"Original unique rows marked: {len(rows_to_mark_yes)}."
    )
    return summary

# ======================================================
# Agent Definition
# ======================================================

job_sheet_agent_instruction_text = """
You are a job-search Google Sheets agent.

Your main job is to:
- Read the spreadsheet whose ID is in JOB_SEARCH_SPREADSHEET_ID.
- Work on a specific sheet/tab (e.g., 'Job_Search_Database').
- Normalize the 'Location' and 'YOE' columns.
- Expand 'remote' jobs so they count toward every city.
- For a given (YOE, Location) pair (e.g., '5 years' in 'san francisco'),
  mark all matching original rows in the sheet by writing 'yes' into the
  'Good_Match_Yes_No' column.

Use the tool mark_good_matches_for_yoe_and_location(sheet_name, target_yoe, target_location)
to perform this operation.
""".strip()

matching_agent: Agent = Agent(
    model=MODEL,
    name="job_sheet_agent",
    description=(
        "Agent that reads a Google Sheet with job search data and marks "
        "good matches for a given years-of-experience and location."
    ),
    generate_content_config=types.GenerateContentConfig(temperature=0.0),
    tools=[mark_good_matches_for_yoe_and_location],
)

__all__ = ["matching_agent"]
