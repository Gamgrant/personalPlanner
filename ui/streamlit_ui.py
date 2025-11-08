import streamlit as st
from services.adk_service import initialize_adk, run_adk_sync
from config.settings import MESSAGE_HISTORY_KEY, get_api_key


DEFAULT_JOB_PROFILE = """\
[Edit this with your real profile]

Name: Steven Yeo
Target roles: Data-driven Process Engineer, R&D Engineer, Computational ChemE, Digital Transformation / AI for Operations
Location: US (OPT), open to relocation
Core skills: DFT & atomistic modeling (VASP, CP2K), zeolites & catalysis, reaction engineering,
             nonlinear/stochastic optimization, Python (pandas, numpy, FastAPI, Streamlit),
             data pipelines & analytics, multi-agent/LLM tooling, Google Cloud.
Highlights:
- Developed mechanistic & kinetic models for zeolite degradation and EFAL migration (BASF-collab project).
- Built agentic workflows (Google ADK) integrating Gmail, Calendar, Drive, and job APIs for automated search & screening.
- Led outreach/organization for research symposiums; strong cross-functional communication.
What to optimize for:
- ATS-friendly resumes & cover letters aligned to each JD.
- Talking points & bullets that quantify impact.
- Roles at companies that can support OPT/H-1B and value modeling + automation skillsets.
"""


def _init_job_profile():
    """
    Ensure a job profile exists in session_state.
    """
    if "job_profile" not in st.session_state:
        st.session_state["job_profile"] = DEFAULT_JOB_PROFILE


def run_streamlit_app():
    """
    Streamlit web application for the ADK chat assistant
    with integrated Job Application Profile context.
    """
    st.set_page_config(
        page_title="ADK Job Search & Chat Assistant",
        layout="wide"
    )

    st.title("👋 Job Search & Chat Assistant (ADK + Gemini)")
    st.markdown(
        "Chat with an ADK-powered assistant that **always knows your job application profile** "
        "and can tailor resumes, outreach, and search strategies to you."
    )
    st.divider()

    # --- API KEY CHECK ---
    api_key = get_api_key()
    if not api_key:
        st.error(
            "⚠️ Action Required: Google API Key Not Found or Invalid.\n"
            "Please set `GOOGLE_API_KEY` in your `.env` file."
        )
        st.stop()

    # --- INIT ADK ---
    adk_runner, current_session_id = initialize_adk()

    # --- SIDEBAR: JOB PROFILE ---
    _init_job_profile()
    with st.sidebar:
        st.header("🧾 Job Application Profile")
        st.caption(
            "This profile is sent as hidden context with every message.\n"
            "Update it to match your current resume + target roles."
        )
        updated_profile = st.text_area(
            "Profile context",
            value=st.session_state["job_profile"],
            height=350,
        )
        if updated_profile != st.session_state["job_profile"]:
            st.session_state["job_profile"] = updated_profile
            st.success("Profile updated for this session.")

        st.markdown("---")
        use_profile = st.checkbox(
            "Attach profile to messages",
            value=True,
            help="If unchecked, messages are sent without your job profile context."
        )

    # --- CHAT UI ---
    st.subheader("💬 Chat with the Assistant")

    if MESSAGE_HISTORY_KEY not in st.session_state:
        st.session_state[MESSAGE_HISTORY_KEY] = []

    # Render history
    for message in st.session_state[MESSAGE_HISTORY_KEY]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # New input
    if prompt := st.chat_input(
        "Ask anything: tailor my resume for X, analyze this JD, draft outreach, suggest roles..."
    ):
        # Show user msg
        st.session_state[MESSAGE_HISTORY_KEY].append(
            {"role": "user", "content": prompt}
        )
        with st.chat_message("user"):
            st.markdown(prompt)

        # Build prompt with job profile context
        if use_profile and st.session_state.get("job_profile"):
            full_prompt = (
                "You are my Job Search & Application Assistant.\n"
                "You must use the following persistent profile when generating any answer, "
                "unless I explicitly say to ignore it.\n\n"
                f"=== JOB APPLICATION PROFILE START ===\n"
                f"{st.session_state['job_profile']}\n"
                f"=== JOB APPLICATION PROFILE END ===\n\n"
                f"Now answer this message using that context:\n{prompt}"
            )
        else:
            full_prompt = prompt

        # Call ADK
        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            with st.spinner("Assistant is thinking..."):
                agent_response = run_adk_sync(
                    adk_runner,
                    current_session_id,
                    full_prompt
                )
                message_placeholder.markdown(agent_response)

        # Save assistant response
        st.session_state[MESSAGE_HISTORY_KEY].append(
            {"role": "assistant", "content": agent_response}
        )