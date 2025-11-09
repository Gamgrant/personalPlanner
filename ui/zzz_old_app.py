# # app.py
# import io
# import os
# import sys
# from pathlib import Path

# import re
# import streamlit as st
# import streamlit.components.v1 as components

# # ---- Make sure Python can see the project root (personalPlanner) ----
# ROOT = Path(__file__).resolve().parents[1]
# if str(ROOT) not in sys.path:
#     sys.path.append(str(ROOT))

# # Now these imports will work
# from utils.routing import ensure_google_oauth_env
# from utils.google_service_helpers import get_drive_service, get_sheets_service
# from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# # Normalize GOOGLE_OAUTH_* env vars to absolute paths
# ensure_google_oauth_env(__file__)

# st.set_page_config(page_title="Simple Career Helper", page_icon="💼", layout="wide")

# # ---------- Google API config ----------
# DRIVE_RESUMES_FOLDER_ID  = "1oWZxO8czQvwjZ-RroN7Lx-jzZxcrzh2Q"
# DRIVE_PROJECTS_FOLDER_ID = "1Q9iuIWIfrClRdUGs38oTzYpsUTT2J6J-"
# SHEET_ID   = "1mVI9o4D4_6g2oS0dqJFCk6jVjgArtN6vnFKV6l7G78c"
# SHEET_RANGE = "Links!A:A"  # sheet named 'Links', column A

# @st.cache_resource
# def get_google_services():
#     """Reuse shared OAuth logic from utils.google_service_helpers."""
#     drive_service = get_drive_service()      # full Drive read/write
#     sheets_service = get_sheets_service()    # Sheets + drive.readonly
#     return drive_service, sheets_service


# # ---------- Hardcoded login (username + password only) ----------
# USE_LOGIN = False  # set True later to re-enable the username/password screen
# ALLOWED_USERNAME = "steven"
# ALLOWED_PASSWORD = "letmein"   # change for your demo


# # styling of primary buttons
# st.markdown("""
#     <style>
#     /* Make primary buttons green */
#     div.stButton > button[kind="primary"] {
#         background-color: #22c55e;
#         color: white;
#     }
#     </style>
# """, unsafe_allow_html=True)

# # ---------- Session defaults ----------
# def ss_defaults():
#     st.session_state.setdefault("authed", not USE_LOGIN)
#     st.session_state.setdefault("page", "home")   # start on home, not "login"

#     # UI source-of-truth for what's shown in Upload view
#     # resumes/projects: list of {"id", "name", "bytes"}
#     st.session_state.setdefault("resumes", [])
#     st.session_state.setdefault("projects", [])
#     # links: list of strings
#     st.session_state.setdefault("links", [])

#     # temp link inputs in Add Documents page
#     st.session_state.setdefault("tmp_links", [""])

#     # 🔹 Delete-mode state
#     st.session_state.setdefault("delete_mode", False)
#     st.session_state.setdefault("delete_resumes", set())
#     st.session_state.setdefault("delete_projects", set())
#     st.session_state.setdefault("delete_links", set())

# ss_defaults()


# # ---------- Helpers ----------
# def is_url(s: str) -> bool:
#     return bool(re.match(r"^https?://", s.strip(), flags=re.I))

# def grid_of_files(items: list, prefix: str, delete_mode: bool = False, selection_key: str | None = None):
#     """
#     Render files in a 3-column grid.

#     If delete_mode is True and selection_key is provided, each file gets
#     a red "mark for deletion" button and we track selections in
#     st.session_state[selection_key] (a set of file IDs).
#     """
#     if delete_mode and selection_key is None:
#         raise ValueError("selection_key is required when delete_mode=True")

#     selected_ids = st.session_state.get(selection_key, set()) if selection_key else set()

#     cols = st.columns(3)
#     for i, item in enumerate(items):
#         with cols[i % 3]:
#             with st.container(border=True):
#                 file_id = item.get("id", f"{prefix}_{i}")

#                 # Top row: name + (optional) delete toggle on the right
#                 c_name, c_del = st.columns([4, 1])

#                 is_selected = delete_mode and (file_id in selected_ids)

#                 with c_name:
#                     # Dim the name if selected (simple aesthetic cue)
#                     if is_selected:
#                         st.markdown(
#                             f"<span style='opacity:0.4;'>**{item['name']}**</span>",
#                             unsafe_allow_html=True,
#                         )
#                     else:
#                         st.markdown(f"**{item['name']}**")

#                     st.download_button(
#                         "View / Download",
#                         data=item["bytes"],
#                         file_name=item["name"],
#                         use_container_width=True,
#                         key=f"{prefix}_download_{i}_{item['name']}",
#                     )

#                 with c_del:
#                     if delete_mode and selection_key:
#                         # Red circle to toggle selection
#                         label = "❌" if not is_selected else "☠️"
#                         if st.button(label, key=f"{prefix}_markdel_{i}"):
#                             # Toggle membership in the set
#                             if is_selected:
#                                 selected_ids.remove(file_id)
#                             else:
#                                 selected_ids.add(file_id)
#                             st.session_state[selection_key] = selected_ids
#                             st.rerun()

# def top_row(back_label="◀ back", on_back=None, right_button_label=None, on_right=None):
#     c1, _, c3 = st.columns([1, 5, 1])
#     with c1:
#         if st.button(back_label, use_container_width=True):
#             if on_back: on_back()
#     with c3:
#         if right_button_label and st.button(right_button_label, use_container_width=True):
#             if on_right: on_right()

# # ---------- Google fetch helpers (used only to seed UI state) ----------
# @st.cache_data(show_spinner=False)
# def list_drive_files(folder_id: str):
#     """
#     List files in a Drive folder and return them in the
#     format expected by grid_of_files: [{"id", "name", "bytes"}, ...]
#     Used only for initial sync into session_state.
#     """
#     drive_service, _ = get_google_services()

#     resp = drive_service.files().list(
#         q=f"'{folder_id}' in parents and trashed = false",
#         fields="files(id, name, mimeType)",
#     ).execute()
#     files = resp.get("files", [])

#     items = []
#     for f in files:
#         file_id = f["id"]
#         name = f["name"]

#         # Download content so we can feed it to st.download_button
#         request = drive_service.files().get_media(fileId=file_id)
#         fh = io.BytesIO()
#         downloader = MediaIoBaseDownload(fh, request)
#         done = False
#         while not done:
#             status, done = downloader.next_chunk()
#         items.append({"id": file_id, "name": name, "bytes": fh.getvalue()})

#     return items


# @st.cache_data(show_spinner=False)
# def fetch_links_from_sheet():
#     """
#     Read all links from the configured sheet range (first column).
#     Used only for initial sync into session_state.
#     """
#     _, sheets_service = get_google_services()

#     result = sheets_service.spreadsheets().values().get(
#         spreadsheetId=SHEET_ID,
#         range=SHEET_RANGE,
#     ).execute()
#     values = result.get("values", [])  # list of rows

#     links = [row[0] for row in values if row]  # first cell of each row
#     return links


# # ---------- PAGES ----------
# def page_home():
#     # Read nav set by the anchor click and route
#     nav = st.query_params.get("nav", None)
#     if nav in {"upload", "customize", "chat"}:
#         st.query_params.clear()
#         st.session_state.page = nav
#         st.rerun()

#     # Styles
#     st.markdown("""
#         <style>
#         #home-title {
#             text-align:center; font-size:32px; font-weight:700;
#             margin-top:1.4rem; margin-bottom:3.5rem;
#         }
#         .bigbtn {
#             display:flex; align-items:center; justify-content:center;
#             width:100%; min-height:40vh;                 /* BIG cards */
#             border-radius:22px; border:1px solid rgba(255,255,255,.15);
#             background: rgba(255,255,255,.06);
#             text-decoration:none !important;
#             color:#fff !important; font-weight:700; font-size:20px;
#             line-height:1.35; text-align:center; padding:0 18px;
#         }
#         .bigbtn:hover { filter:brightness(1.12); }
#         @media (max-width:900px){
#             .bigbtn { min-height:45vh; font-size:18px; }
#         }
#         </style>
#     """, unsafe_allow_html=True)

#     st.markdown('<div id="home-title">👋 Hey Steven, what do you want to do today?</div>', unsafe_allow_html=True)

#     # Anchor cards (stay in same tab via target="_self")
#     c1, c2, c3 = st.columns(3)
#     with c1:
#         st.markdown('<a class="bigbtn" href="?nav=upload" target="_self">'
#                     'Upload/view documents</a>', unsafe_allow_html=True)
#     with c2:
#         st.markdown('<a class="bigbtn" href="?nav=customize" target="_self">'
#                     'Customize resumes & Outreach for jobs</a>', unsafe_allow_html=True)
#     with c3:
#         st.markdown('<a class="bigbtn" href="?nav=chat" target="_self">'
#                     'Chat — Ask me anything</a>', unsafe_allow_html=True)


# def page_upload():
#     def back():
#         st.session_state.page = "home"
#         st.rerun()

#     def open_add_docs():
#         st.session_state.page = "add_docs"
#         st.session_state.tmp_links = [""]
#         st.rerun()

#     def start_delete_mode():
#         # Enter delete mode and clear any previous selections
#         st.session_state.delete_mode = True
#         st.session_state.delete_resumes = set()
#         st.session_state.delete_projects = set()
#         st.session_state.delete_links = set()
#         st.rerun()

#     def finish_delete_mode():
#         # Called when user presses "Done"
#         with st.spinner("Deleting selected documents and links..."):
#             drive_service, sheets_service = get_google_services()

#             # Snapshot current UI state
#             resumes = st.session_state.resumes
#             projects = st.session_state.projects
#             links = st.session_state.links

#             delete_resumes = st.session_state.delete_resumes
#             delete_projects = st.session_state.delete_projects
#             delete_links = st.session_state.delete_links  # set of indices

#             # --- Delete from Drive ---
#             for file_id in delete_resumes:
#                 try:
#                     drive_service.files().delete(fileId=file_id).execute()
#                 except Exception:
#                     pass  # could log
#             for file_id in delete_projects:
#                 try:
#                     drive_service.files().delete(fileId=file_id).execute()
#                 except Exception:
#                     pass

#             # --- Compute remaining items for UI ---
#             new_resumes = [item for item in resumes if item["id"] not in delete_resumes]
#             new_projects = [item for item in projects if item["id"] not in delete_projects]
#             remaining_links = [
#                 link for idx, link in enumerate(links) if idx not in delete_links
#             ]

#             # --- Apply to Google Sheets ---
#             # Clear and rewrite the column based on remaining_links
#             sheets_service.spreadsheets().values().clear(
#                 spreadsheetId=SHEET_ID,
#                 range=SHEET_RANGE,
#             ).execute()

#             if remaining_links:
#                 body = {"values": [[link] for link in remaining_links]}
#                 sheets_service.spreadsheets().values().update(
#                     spreadsheetId=SHEET_ID,
#                     range=SHEET_RANGE,
#                     valueInputOption="RAW",
#                     body=body,
#                 ).execute()

#             # --- Update UI state ---
#             st.session_state.resumes = new_resumes
#             st.session_state.projects = new_projects
#             st.session_state.links = remaining_links

#             # Reset delete mode + selections
#             st.session_state.delete_mode = False
#             st.session_state.delete_resumes = set()
#             st.session_state.delete_projects = set()
#             st.session_state.delete_links = set()

#         st.rerun()

#     # ---- Custom top row with Back, Delete, Add ----
#     c_back, c_spacer, c_delete, c_add = st.columns([1, 4, 1, 1])
#     with c_back:
#         if st.button("◀ back", use_container_width=True):
#             back()
#     with c_delete:
#         if st.session_state.delete_mode:
#             done_label = "✅ Done"
#             if st.button(done_label, key="done_delete", use_container_width=True):
#                 finish_delete_mode()
#         else:
#             if st.button("➖ Delete documents", key="start_delete", use_container_width=True):
#                 start_delete_mode()
#     with c_add:
#         if st.button("➕ Add documents", use_container_width=True):
#             open_add_docs()

#     # ---- Initial sync from Google into session_state (only if needed) ----
#     needs_fetch = (
#         not st.session_state.resumes
#         or not st.session_state.projects
#         or not st.session_state.links
#     )

#     if needs_fetch:
#         with st.spinner("Loading your documents from Google Drive and Sheets..."):
#             if not st.session_state.resumes:
#                 st.session_state.resumes = list_drive_files(DRIVE_RESUMES_FOLDER_ID)
#             if not st.session_state.projects:
#                 st.session_state.projects = list_drive_files(DRIVE_PROJECTS_FOLDER_ID)
#             if not st.session_state.links:
#                 st.session_state.links = fetch_links_from_sheet()

#     # Use UI state as source of truth
#     resumes = st.session_state.resumes
#     projects = st.session_state.projects
#     links = st.session_state.links
#     delete_mode = st.session_state["delete_mode"]

#     st.subheader("Resumes")
#     if resumes:
#         grid_of_files(
#             resumes,
#             prefix="resumes",
#             delete_mode=delete_mode,
#             selection_key="delete_resumes",
#         )
#     else:
#         st.info("No resumes yet. Click **Add documents** to upload.")

#     st.markdown("## Projects")
#     if projects:
#         grid_of_files(
#             projects,
#             prefix="projects",
#             delete_mode=delete_mode,
#             selection_key="delete_projects",
#         )
#     else:
#         st.info("No project files yet. Click **Add documents** to upload.")

#     st.markdown("## Provided links")
#     if links:
#         delete_links = st.session_state["delete_links"]

#         for idx, url in enumerate(links):
#             c_link, c_del = st.columns([10, 1])
#             is_selected = delete_mode and (idx in delete_links)

#             with c_link:
#                 if is_selected:
#                     st.markdown(
#                         f"<span style='opacity:0.4;'>[{url}]({url})</span>",
#                         unsafe_allow_html=True,
#                     )
#                 else:
#                     st.markdown(f"- [{url}]({url})")

#             with c_del:
#                 if delete_mode:
#                     label = "❌" if not is_selected else "☠️"
#                     if st.button(label, key=f"link_markdel_{idx}"):
#                         if is_selected:
#                             delete_links.remove(idx)
#                         else:
#                             delete_links.add(idx)
#                         st.session_state["delete_links"] = delete_links
#                         st.rerun()
#     else:
#         st.info("No links yet.")


# def upload_files_to_drive(files, folder_id, drive_service):
#     """
#     Upload Streamlit UploadedFile objects to a specific Drive folder.

#     Returns a list of {"id", "name", "bytes"} so we can update UI immediately.
#     """
#     new_items = []
#     for f in files or []:
#         file_metadata = {
#             "name": f.name,
#             "parents": [folder_id],
#         }
#         media = MediaIoBaseUpload(
#             io.BytesIO(f.getvalue()),
#             mimetype=f.type or "application/octet-stream",
#             resumable=False,
#         )
#         created = drive_service.files().create(
#             body=file_metadata,
#             media_body=media,
#             fields="id,name",
#         ).execute()
#         file_id = created["id"]
#         name = created["name"]
#         new_items.append({"id": file_id, "name": name, "bytes": f.getvalue()})
#     return new_items


# def append_links_to_sheet(links, sheets_service):
#     """Append each link as a new row into Links!A:A in the Links sheet."""
#     links = [l for l in links if l.strip()]
#     if not links:
#         return
#     body = {"values": [[link] for link in links]}
#     sheets_service.spreadsheets().values().append(
#         spreadsheetId=SHEET_ID,
#         range=SHEET_RANGE,
#         valueInputOption="RAW",
#         insertDataOption="INSERT_ROWS",
#         body=body,
#     ).execute()


# def page_add_docs():
#     def cancel_and_back():
#         # Just go back; we haven't changed UI state yet
#         st.session_state.page = "upload"
#         st.rerun()

#     def add_link_field():
#         st.session_state.tmp_links.append("")

#     def submit():
#         with st.spinner("Uploading your documents and saving your links..."):
#             # Grab uploaded files
#             proj_files = st.session_state.get("_proj_files", []) or []
#             res_files  = st.session_state.get("_res_files", []) or []

#             # Clean + validate links
#             cleaned_links = [
#                 s.strip()
#                 for s in st.session_state.tmp_links
#                 if s.strip() and is_url(s)
#             ]

#             # Get Google services via shared OAuth helper
#             drive_service, sheets_service = get_google_services()

#             # Upload files to the correct Drive folders (and get items for UI)
#             new_resumes = upload_files_to_drive(res_files,  DRIVE_RESUMES_FOLDER_ID,  drive_service)
#             new_projects = upload_files_to_drive(proj_files, DRIVE_PROJECTS_FOLDER_ID, drive_service)

#             # Append links to the Links sheet
#             append_links_to_sheet(cleaned_links, sheets_service)

#             # 🔹 Update UI state immediately (optimistic)
#             st.session_state.resumes.extend(new_resumes)
#             st.session_state.projects.extend(new_projects)
#             st.session_state.links.extend(cleaned_links)

#             # Navigate back to Upload page; rerun will render from updated UI state
#             st.session_state.page = "upload"

#     with st.container(border=True):
#         c1, _, c3 = st.columns([1, 5, 1])
#         with c1:
#             if st.button("←", help="Back (discard changes)"):
#                 cancel_and_back()
#         with c3:
#             st.button("Submit", type="primary", use_container_width=True, on_click=submit)

#         st.markdown("### Please drop **projects** here")
#         st.file_uploader(
#             "Projects",
#             accept_multiple_files=True,
#             key="_proj_files",
#             label_visibility="collapsed",
#         )

#         st.markdown("### Please drop your **resumes** here")
#         st.file_uploader(
#             "Resumes",
#             accept_multiple_files=True,
#             key="_res_files",
#             label_visibility="collapsed",
#         )

#         st.markdown("### Add links")
#         for i, _ in enumerate(st.session_state.tmp_links):
#             st.session_state.tmp_links[i] = st.text_input(
#                 f"Link {i+1}",
#                 value=st.session_state.tmp_links[i],
#                 placeholder="https://example.com/portfolio",
#                 key=f"link_{i}",
#             )

#         st.button("➕ Add another link", on_click=add_link_field)


# def page_customize():
#     def back(): st.session_state.page = "home"; st.rerun()
#     top_row(on_back=back)
#     st.subheader("Customize resumes & Outreach for jobs")
#     st.info("Placeholder page. Wire in your logic later.")


# def page_chat():
#     def back(): st.session_state.page = "home"; st.rerun()
#     top_row(on_back=back)
#     st.subheader("Chat — Ask me anything")
#     st.info("Placeholder chat page.")


# # ---------- Router ----------
# if USE_LOGIN and not st.session_state.authed:
#     st.write("Login is disabled in this build. Set USE_LOGIN=True to enable.")
# else:
#     page = st.session_state.page
#     if page == "home": page_home()
#     elif page == "upload": page_upload()
#     elif page == "add_docs": page_add_docs()
#     elif page == "customize": page_customize()
#     elif page == "chat": page_chat()
#     else:
#         st.session_state.page = "home"; st.rerun()
