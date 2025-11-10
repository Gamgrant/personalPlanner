from __future__ import annotations

import os
import re
import html as htmllib
import requests
from typing import List, Optional, Dict, Any

from googleapiclient.errors import HttpError
from google.adk.agents import Agent
from google.genai import types

from utils.google_service_helpers import get_google_service

MODEL = os.environ.get("MODEL", "gemini-2.5-flash")
JOB_SEARCH_SPREADSHEET_ID = os.environ.get("JOB_SEARCH_SPREADSHEET_ID").strip()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# -------------------------------
# Google API clients
# -------------------------------

def get_sheets_service() -> object:
    return get_google_service("sheets", "v4", SCOPES, "BACKFILL_SHEETS")


def get_drive_service() -> object:
    return get_google_service("drive", "v3", SCOPES, "BACKFILL_DRIVE")


# -------------------------------
# Spreadsheet discovery helpers
# -------------------------------

import os  # ensure imported at top

def _find_job_search_spreadsheet_id(name: str = "Job_Search_Database") -> str:
    """
    Locate the job search spreadsheet.

    Priority:
      1) Hardcoded / env JOB_SEARCH_SPREADSHEET_ID
      2) Name-based lookup (optionally restricted to JOB_SEARCH_FOLDER_ID)
    """
    # 1) Hardcoded/env override (primary path)
    if JOB_SEARCH_SPREADSHEET_ID:
        return JOB_SEARCH_SPREADSHEET_ID

    # 2) Fallback: Drive search by name (should rarely be used now)
    drive = get_drive_service()
    folder_id = os.environ.get("JOB_SEARCH_FOLDER_ID")

    base_query = "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
    if folder_id:
        base_query += f" and '{folder_id}' in parents"

    try:
        resp = drive.files().list(
            q=base_query,
            pageSize=50,
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[BACKFILL] Drive API error: {e}")

    files: List[Dict[str, Any]] = resp.get("files", []) or []
    if not files:
        raise RuntimeError(
            f"[BACKFILL] Spreadsheet not found. "
            f"Set JOB_SEARCH_SPREADSHEET_ID, or create a Google Sheet named '{name}'."
        )

    target_lower = name.lower()
    for f in files:
        if (f.get("name") or "").lower() == target_lower:
            return f["id"]

    return files[0]["id"]


def _get_first_sheet_name(spreadsheet_id: str) -> str:
    """
    Return the title of the first sheet/tab in the spreadsheet.
    """
    sheets = get_sheets_service()
    resp = sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(title))",
    ).execute()
    sheets_list = resp.get("sheets", []) or []
    if not sheets_list:
        raise RuntimeError("[BACKFILL] Target spreadsheet has no sheets.")
    return sheets_list[0]["properties"]["title"]


# -------------------------------
# Extraction helpers
# (lightweight heuristics to mimic LLM-style reasoning over text)
# -------------------------------

_DEGREE_LEVELS = [
    ("phd", r"\b(ph\.?d\.?|doctorate|doctoral)\b"),
    ("master's", r"\b(master'?s|ms|m\.s\.|msc|m\.sc\.|m\.eng|meng|mba)\b"),
    ("bachelor's", r"\b(bachelor'?s|bs|b\.s\.|ba|b\.a\.|bsc|b\.sc\.|b\.eng|beng)\b"),
    ("associate", r"\b(associate'?s|aas|a\.a\.s\.|as|a\.s\.)\b"),
    ("high school", r"\b(high\s*school|ged)\b"),
]

_SKILL_ALIASES: Dict[str, str] = {
    # --- Core programming languages ---
    "Python": r"\bpython\b",
    "R": r"(?<!\w)r(?!\w)",
    "SQL": r"\bsql\b",
    "NoSQL": r"\bnosql\b",
    "Java": r"\bjava(?!script)\b",
    "JavaScript": r"\bjavascript|node\.?js|nodejs\b",
    "TypeScript": r"\btypescript\b",
    "Go": r"\bgolang|\bgo\b",
    "C++": r"\bc\+\+\b",
    "C#": r"\bc#\b",
    "Scala": r"\bscala\b",
    "Ruby": r"\bruby\b",
    "PHP": r"\bphp\b",
    "Rust": r"\brust\b",
    "Kotlin": r"\bkotlin\b",
    "Swift": r"\bswift\b",

    # --- DS / ML / AI concepts ---
    "Machine Learning": r"\bmachine\s+learning\b",
    "Deep Learning": r"\bdeep\s+learning\b",
    "Statistics": r"\bstatistics|statistical\b",
    "Statistical Analysis": r"\bstatistical\s+analysis\b",
    "Probability": r"\bprobability\b",
    "Hypothesis Testing": r"\bhypothesis\s+testing\b",
    "A/B Testing": r"\ba\/b\s*testing|ab\s*testing\b",
    "Experiment Design": r"\bexperimental?\s+design\b",
    "Regression": r"\bregression\b",
    "Time Series": r"\btime[-\s]*series\b",
    "Bayesian": r"\bbayesian\b",
    "NLP": r"\bnatural\s+language\s+processing|nlp\b",
    "Computer Vision": r"\bcomputer\s+vision\b",
    "Reinforcement Learning": r"\breinforcement\s+learning\b",
    "Recommendation Systems": r"\brecommendation\s+systems?\b",
    "Anomaly Detection": r"\banomaly\s+detection\b",

    # --- DS / ML libraries & tooling ---
    "Pandas": r"\bpandas\b",
    "NumPy": r"\bnumpy\b",
    "SciPy": r"\bscipy\b",
    "Scikit-learn": r"\bscikit[-\s]?learn|sklearn\b",
    "XGBoost": r"\bxgboost\b",
    "LightGBM": r"\blightgbm\b",
    "CatBoost": r"\bcatboost\b",
    "TensorFlow": r"\btensorflow\b",
    "Keras": r"\bkeras\b",
    "PyTorch": r"\bpytorch\b",
    "JAX": r"\bjax\b",
    "spaCy": r"\bspacy\b",
    "NLTK": r"\bnltk\b",
    "Transformers": r"\btransformers\b",
    "OpenCV": r"\bopencv\b",

    # --- GenAI / LLM / vector DBs ---
    "LangChain": r"\blangchain\b",
    "LlamaIndex": r"\bllama\s*index|llamaindex\b",
    "Hugging Face": r"\bhugging\s*face\b",
    "OpenAI API": r"\bopenai\s+api\b",
    "Azure OpenAI": r"\bazure\s+openai\b",
    "Vertex AI": r"\bvertex\s+ai\b",
    "Pinecone": r"\bpinecone\b",
    "Weaviate": r"\bweaviate\b",
    "Milvus": r"\bmilvus\b",
    "Chroma": r"\bchroma(?:db)?\b",
    "FAISS": r"\bfaiss\b",

    # --- Data viz / BI ---
    "Matplotlib": r"\bmatplotlib\b",
    "Seaborn": r"\bseaborn\b",
    "Plotly": r"\bplotly\b",
    "Bokeh": r"\bbokeh\b",
    "D3.js": r"\bd3\.js|d3js\b",
    "Tableau": r"\btableau\b",
    "Power BI": r"\bpower\s*bi\b",
    "Looker": r"\blooker\b",
    "LookML": r"\blookml\b",
    "Excel": r"\bexcel\b",

    # --- Databases / warehouses / search ---
    "PostgreSQL": r"\bpostgres(?:ql)?\b",
    "MySQL": r"\bmysql\b",
    "SQLite": r"\bsqlite\b",
    "MongoDB": r"\bmongodb\b",
    "Cassandra": r"\bcassandra\b",
    "Redis": r"\bredis\b",
    "Elasticsearch": r"\belasticsearch\b",
    "Solr": r"\bsolr\b",
    "Snowflake": r"\bsnowflake\b",
    "Redshift": r"\bredshift\b",
    "BigQuery": r"\bbigquery\b",
    "DynamoDB": r"\bdynamodb\b",
    "ClickHouse": r"\bclickhouse\b",

    # --- Data engineering / pipelines ---
    "Airflow": r"\bairflow\b",
    "dbt": r"\bdbt\b",
    "Fivetran": r"\bfivetran\b",
    "Stitch": r"\bstitch\b",
    "ETL": r"\betl\b",
    "Kafka": r"\bkafka\b",
    "Kinesis": r"\bkinesis\b",
    "Spark": r"\bspark\b",
    "Hadoop": r"\bhadoop\b",

    # --- Cloud / infra / MLOps ---
    "AWS": r"\baws\b",
    "GCP": r"\bgcp|google\s+cloud\b",
    "Azure": r"\bazure\b",
    "Docker": r"\bdocker\b",
    "Kubernetes": r"\bkubernetes|k8s\b",
    "Helm": r"\bhelm\b",
    "Terraform": r"\bterraform\b",
    "Ansible": r"\bansible\b",
    "Jenkins": r"\bjenkins\b",
    "CircleCI": r"\bcircleci\b",
    "Travis CI": r"\btravis\s*ci\b",
    "GitHub Actions": r"\bgithub\s+actions\b",
    "Argo CD": r"\bargo\s*cd\b",
    "SageMaker": r"\bsagemaker\b",
    "MLflow": r"\bmlflow\b",
    "Kubeflow": r"\bkubeflow\b",
    "Ray": r"\bray\b",
    "DVC": r"\bdvc\b",

    # --- Web / app frameworks (SWE) ---
    "React": r"\breact\b",
    "React Native": r"\breact\s+native\b",
    "Next.js": r"\bnext\.js|nextjs\b",
    "Vue": r"\bvue\.?js\b",
    "Nuxt": r"\bnuxt\.?js\b",
    "Angular": r"\bangular\b",
    "Svelte": r"\bsvelte\b",
    "Django": r"\bdjango\b",
    "Flask": r"\bflask\b",
    "FastAPI": r"\bfastapi\b",
    "Rails": r"\brails|ruby\s+on\s+rails\b",
    "Spring": r"\bspring\b",
    "Spring Boot": r"\bspring\s+boot\b",
    ".NET": r"\b\.net\b",
    "ASP.NET": r"\basp\.net\b",
    "Express.js": r"\bexpress\.js|expressjs\b",
    "NestJS": r"\bnestjs\b",
    "Laravel": r"\blaravel\b",

    # --- APIs / protocols ---
    "REST": r"\brestful?\b",
    "GraphQL": r"\bgraphql\b",
    "gRPC": r"\bgrpc\b",
    "WebSockets": r"\bweb\s*sockets?\b",
    "OpenAPI": r"\bopenapi\b",
    "Swagger": r"\bswagger\b",

    # --- Testing / QA ---
    "Jest": r"\bjest\b",
    "Mocha": r"\bmocha\b",
    "Chai": r"\bchai\b",
    "Cypress": r"\bcypress\b",
    "Playwright": r"\bplaywright\b",
    "JUnit": r"\bjunit\b",
    "pytest": r"\bpytest\b",
    "unittest": r"\bunittest\b",
    "Selenium": r"\bselenium\b",

    # --- Build / tooling ---
    "Webpack": r"\bwebpack\b",
    "Babel": r"\bbabel\b",
    "Rollup": r"\brollup\b",
    "Vite": r"\bvite\b",
    "Gradle": r"\bgradle\b",
    "Maven": r"\bmaven\b",
    "npm": r"\bnpm\b",
    "Yarn": r"\byarn\b",
    "pnpm": r"\bpnpm\b",

    # --- Monitoring / logging / ops ---
    "Prometheus": r"\bprometheus\b",
    "Grafana": r"\bgrafana\b",
    "Datadog": r"\bdatadog\b",
    "New Relic": r"\bnew\s+relic\b",
    "Splunk": r"\bsplunk\b",
    "Sentry": r"\bsentry\b",

    # --- Version control & collaboration ---
    "Git": r"\bgit\b",
    "GitHub": r"\bgithub\b",
    "GitLab": r"\bgitlab\b",
    "Bitbucket": r"\bbitbucket\b",
    "JIRA": r"\bjira\b",
    "Confluence": r"\bconfluence\b",

    # --- Soft skills (kept, but deprioritized) ---
    "Project Management": r"\bproject\s+management\b",
    "Leadership": r"\blead(?:er|ership)\b",
    "Communication": r"\bcommunication|communicator\b",
}

_YOE_PATTERNS = [
    r"\b(?P<min>\d+(?:\.\d+)?)\s*(?:\+|(?:-|–|—|to)\s*(?P<max>\d+(?:\.\d+)?))?\s*"
    r"(?:years?|yrs?)'?(?:\s+of)?\s*(?:full[-\s]*time\s*)?(?:experience|exp)?\b",
    r"\b(?:minimum|at\s+least)(?:\s+of)?\s*(?P<min>\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\b",
    r"\b(?P<max>\d+(?:\.\d+)?)\s*(?:years?|yrs?)\s*(?:experience)?\s*preferred\b",
    r"\b(entry[-\s]?level|new\s*grad)\b",
    r"\bintern(ship)?\b",
]

def _extract_years_experience(text: str) -> str:
    for p in _YOE_PATTERNS[:3]:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            gd = m.groupdict()
            minv = gd.get("min")
            maxv = gd.get("max")
            if minv and maxv:
                return f"{minv}-{maxv} years"
            if minv:
                return f"{minv}+ years"
            if maxv:
                return f"up to {maxv} years"
    if re.search(_YOE_PATTERNS[3], text, re.IGNORECASE):
        return "0-1 years (entry level)"
    if re.search(_YOE_PATTERNS[4], text, re.IGNORECASE):
        return "0 years (internship)"
    return ""

def _extract_degree(text: str) -> str:
    found = []
    for label, pat in _DEGREE_LEVELS:
        if re.search(pat, text, re.IGNORECASE):
            found.append(label)
    if not found:
        return ""
    order = {lvl: i for i, (lvl, _) in enumerate(_DEGREE_LEVELS)}
    # choose highest requirement
    highest = sorted(found, key=lambda x: order[x])[0]
    if highest == "phd":
        return "PhD"
    if highest == "master's":
        return "Master's"
    if highest == "bachelor's":
        return "Bachelor's"
    if highest == "associate":
        return "Associate"
    if highest == "high school":
        return "High School"
    return highest.title()

def _extract_skills(text: str, max_count: int = 30) -> str:
    """
    Extract a condensed list of primarily technical skills from the description.

    - Prefer concrete technical tools / languages / frameworks.
    - Soft skills (Communication, Leadership, Project Management) are appended last.
    - Aim for ~20–30 tokens when available (controlled by max_count).
    """
    if not text:
        return ""

    # 1) Find all alias hits in the description
    hits = {name for name, pat in _SKILL_ALIASES.items() if re.search(pat, text, re.IGNORECASE)}
    if not hits:
        return ""

    # Separate soft skills from technical
    soft_skills = {"Communication", "Leadership", "Project Management"}
    tech_hits = hits - soft_skills
    soft_hits = hits & soft_skills

    # Priority ordering for common technical skills (AI, DS, DE, SWE)
    tech_priority = [
        # Core DS / ML / AI
        "Machine Learning", "Deep Learning",
        "Statistics", "Statistical Analysis", "Probability",
        "Hypothesis Testing", "A/B Testing", "Experiment Design",
        "Regression", "Time Series", "Bayesian",
        "NLP", "Computer Vision", "Reinforcement Learning",
        "Recommendation Systems", "Anomaly Detection",

        # DS / ML libs
        "Python", "R", "SQL",
        "Pandas", "NumPy", "SciPy",
        "Scikit-learn", "XGBoost", "LightGBM", "CatBoost",
        "TensorFlow", "Keras", "PyTorch", "JAX",
        "spaCy", "NLTK", "Transformers", "OpenCV",

        # GenAI / LLM
        "LangChain", "LlamaIndex", "Hugging Face",
        "OpenAI API", "Azure OpenAI", "Vertex AI",
        "Pinecone", "Weaviate", "Milvus", "Chroma", "FAISS",

        # Data viz / BI
        "Matplotlib", "Seaborn", "Plotly", "Bokeh", "D3.js",
        "Tableau", "Power BI", "Looker", "LookML", "Excel",

        # Data stores / warehouses / search
        "PostgreSQL", "MySQL", "SQLite",
        "MongoDB", "Cassandra", "Redis", "Elasticsearch", "Solr",
        "Snowflake", "Redshift", "BigQuery", "DynamoDB", "ClickHouse", "NoSQL",

        # Data eng / pipelines
        "Airflow", "dbt", "Fivetran", "Stitch", "ETL",
        "Kafka", "Kinesis", "Spark", "Hadoop",

        # Cloud / infra / MLOps
        "AWS", "GCP", "Azure",
        "Docker", "Kubernetes", "Helm", "Terraform", "Ansible",
        "Jenkins", "CircleCI", "Travis CI", "GitHub Actions", "Argo CD",
        "SageMaker", "MLflow", "Kubeflow", "Ray", "DVC",

        # SWE languages
        "Java", "JavaScript", "TypeScript", "Go", "C++", "C#", "Scala",
        "Ruby", "PHP", "Rust", "Kotlin", "Swift",

        # Web / app frameworks
        "React", "React Native", "Next.js", "Vue", "Nuxt", "Angular", "Svelte",
        "Django", "Flask", "FastAPI", "Rails", "Spring", "Spring Boot",
        ".NET", "ASP.NET", "Express.js", "NestJS", "Laravel",

        # APIs / protocols
        "REST", "GraphQL", "gRPC", "WebSockets", "OpenAPI", "Swagger",

        # Testing / QA
        "Jest", "Mocha", "Chai", "Cypress", "Playwright",
        "JUnit", "pytest", "unittest", "Selenium",

        # Build / tooling
        "Webpack", "Babel", "Rollup", "Vite",
        "Gradle", "Maven", "npm", "Yarn", "pnpm",

        # Monitoring / logging / ops
        "Prometheus", "Grafana", "Datadog", "New Relic",
        "Splunk", "Sentry",

        # Version control & collab
        "Git", "GitHub", "GitLab", "Bitbucket", "JIRA", "Confluence",
    ]

    ordered: List[str] = []

    # 2) Add technical skills in priority order
    for s in tech_priority:
        if s in tech_hits:
            ordered.append(s)

    # 3) Add any remaining technical skills (not in the priority list) in alpha order
    remaining_tech = sorted(tech_hits - set(ordered))
    ordered.extend(remaining_tech)

    # 4) Finally add soft skills at the end, if present
    for s in ["Project Management", "Leadership", "Communication"]:
        if s in soft_hits:
            ordered.append(s)

    if not ordered:
        return ""

    # Cap to max_count
    return ", ".join(ordered[:max_count])

def _extract_all_fields(text: str) -> Dict[str, str]:
    """
    Extract Degree, YOE, Skills using semantic/heuristic reasoning over the full description.
    (This replaces brittle tool-style parsing; treat it as LLM-style interpretation.)
    """
    return {
        "degree": _extract_degree(text),
        "yoe": _extract_years_experience(text),
        "skills": _extract_skills(text),
    }


# -------------------------------
# HTML / description helpers
# -------------------------------

def _html_to_text_full(html: str) -> str:
    """
    Convert HTML to full plain text with structure preserved (no truncation).
    """
    if not html:
        return ""

    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)

    block_tags = [
        "p","div","br","li","ul","ol","section","article",
        "h1","h2","h3","h4","h5","h6","table","tr","td","th"
    ]
    for tag in block_tags:
        html = re.sub(fr"</?{tag}[^>]*>", "\n", html, flags=re.IGNORECASE)

    text = re.sub(r"<[^>]+>", " ", html)
    text = htmllib.unescape(text)
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


GH_COMPANY_FROM_DOMAIN: Dict[str, str] = {
    "stripe.com": "stripe",
    "databricks.com": "databricks",
    "asana.com": "asana",
    "anthropic.com": "anthropic",
}

def _infer_greenhouse_company_from_url(url: str) -> Optional[str]:
    url_lower = url.lower()
    for domain, board in GH_COMPANY_FROM_DOMAIN.items():
        if domain in url_lower:
            return board
    m = re.search(r"job-boards\.greenhouse\.io/([^/]+)/jobs/", url_lower)
    if m:
        return m.group(1)
    return None


def _fetch_description_from_url(url: str) -> str:
    if not url:
        return ""

    gh_match = re.search(r"[?&]gh_jid=(\d+)", url)
    if gh_match:
        job_id = gh_match.group(1)
        company = _infer_greenhouse_company_from_url(url)
        if company:
            api_url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}?content=true"
            try:
                r = requests.get(api_url, timeout=20)
                r.raise_for_status()
                data = r.json() or {}
                html = data.get("content") or data.get("description") or ""
                if html:
                    return _html_to_text_full(html)
            except Exception:
                pass

    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        return _html_to_text_full(r.text)
    except Exception:
        return ""


# -------------------------------
# Tool 1: backfill FULL descriptions into F
# -------------------------------
def enrich_job_search_database(
    max_rows: Optional[int] = None,
    overwrite: bool = False,
) -> str:
    """
    Deterministic wrapper: always
      1) backfill descriptions in F
      2) extract Degree/YOE/Skills into G/H/I
    """
    msg1 = backfill_job_descriptions(max_rows=max_rows)
    msg2 = extract_structured_fields(max_rows=max_rows, overwrite=overwrite)
    return f"{msg1}\n\n{msg2}"

def backfill_job_descriptions(
    max_rows: Optional[int] = None,
) -> str:
    """
    Backfill FULL Description column (F) in Job_search_Database.
    """
    sheets = get_sheets_service()
    spreadsheet_id = _find_job_search_spreadsheet_id("Job_search_Database")
    sheet_name = _get_first_sheet_name(spreadsheet_id)

    data_range = f"{sheet_name}!A2:G"
    try:
        result = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=data_range,
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[BACKFILL] Failed to read sheet values: {e}")

    rows: List[List[str]] = result.get("values", []) or []
    if not rows:
        return "[BACKFILL] No rows found."

    limit = len(rows) if not max_rows or max_rows <= 0 else min(max_rows, len(rows))

    updates: List[Dict[str, Any]] = []
    updated_count = 0

    for idx in range(limit):
        row = rows[idx]
        if len(row) < 7:
            row = row + [""] * (7 - len(row))

        website = (row[1] or "").strip()      # B
        description = (row[5] or "").strip()  # F

        if website and not description:
            full_desc = _fetch_description_from_url(website)
            if full_desc:
                row_number = idx + 2
                updates.append({
                    "range": f"{sheet_name}!F{row_number}",
                    "values": [[full_desc]],
                })
                updated_count += 1

    if not updates:
        return "[BACKFILL] No descriptions updated."

    try:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[BACKFILL] Failed to write updated descriptions: {e}")

    return f"[BACKFILL] Updated descriptions for {updated_count} job(s) in '{sheet_name}'."


# -------------------------------
# Tool 2: extract Degree/YOE/Skills into G/H/I using description text
# -------------------------------

def extract_structured_fields(
    max_rows: Optional[int] = None,
    overwrite: bool = False,
) -> str:
    """
    For each row in Job_search_Database:

      - Read FULL Description from F
      - Use reasoning over the text to infer:
            G: Degree
            H: YOE
            I: Skills (comma-separated)
      - Write values into G/H/I
      - Return ONLY blocks in the strict format:

            Degree:
            YOE:
            Skills from the description:

        (one block per updated row, no extra commentary)
    """
    sheets = get_sheets_service()
    spreadsheet_id = _find_job_search_spreadsheet_id("Job_search_Database")
    sheet_name = _get_first_sheet_name(spreadsheet_id)

    data_range = f"{sheet_name}!A2:I"
    try:
        result = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=data_range,
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"[FIELDS] Failed to read sheet values: {e}")

    rows: List[List[str]] = result.get("values", []) or []
    if not rows:
        return "Degree:\nYOE:\nSkills from the description:"

    limit = len(rows) if not max_rows or max_rows <= 0 else min(max_rows, len(rows))

    updates: List[Dict[str, Any]] = []
    response_lines: List[str] = []

    for i in range(limit):
        row = rows[i]
        if len(row) < 9:
            row = row + [""] * (9 - len(row))

        desc = (row[5] or "").strip()  # F
        cur_deg = (row[6] or "").strip()
        cur_yoe = (row[7] or "").strip()
        cur_sk = (row[8] or "").strip()

        if not desc:
            continue

        want_deg = overwrite or not cur_deg
        want_yoe = overwrite or not cur_yoe
        want_sk = overwrite or not cur_sk

        if not (want_deg or want_yoe or want_sk):
            continue

        extracted = _extract_all_fields(desc)
        deg = extracted["degree"] if want_deg else cur_deg
        yoe = extracted["yoe"] if want_yoe else cur_yoe
        sks = extracted["skills"] if want_sk else cur_sk

        rownum = i + 2

        if want_deg:
            updates.append({"range": f"{sheet_name}!G{rownum}", "values": [[deg]]})
        if want_yoe:
            updates.append({"range": f"{sheet_name}!H{rownum}", "values": [[yoe]]})
        if want_sk:
            updates.append({"range": f"{sheet_name}!I{rownum}", "values": [[sks]]})

        # Append strictly formatted block for this row
        response_lines.append(f"Degree: {deg}")
        response_lines.append(f"YOE: {yoe}")
        response_lines.append(f"Skills from the description: {sks}")
        response_lines.append("")  # blank line between rows

    if updates:
        try:
            sheets.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": updates},
            ).execute()
        except HttpError as e:
            raise RuntimeError(f"[FIELDS] Failed to write structured fields: {e}")

    # If nothing was updated, still respect strict output format
    if not response_lines:
        return "Degree:\nYOE:\nSkills from the description:"

    # Strict: only these lines, no extra commentary
    return "\n".join(response_lines).rstrip()


# -------------------------------
# Agent definition
# -------------------------------

backfill_agent_instruction = backfill_agent_instruction = """
You enrich the existing 'Job_search_Database' Google Sheet.

Layout (first sheet):
  A: Jobs
  B: Website
  C: Company
  D: Location
  E: Date Posted
  F: Description (FULL plain text, no truncation)
  G: Degree
  H: YOE
  I: Skill

Behavior:

1. Backfill Description (F)
- If Description (F) is empty and Website (B) has a URL:
    • Fetch the page (use Greenhouse API when gh_jid appears; otherwise fetch HTML directly).
    • Convert the full HTML to plain text without truncation.
    • Write the full plain-text job description into column F.

2. Extract structured fields from Description (F)
For each row where Description (F) is present:

- Use your own reasoning capabilities over the full text (no other sub-agents, no external tools) to infer three things:

  (a) Skills
    - Combine both required and preferred skills into a single condensed list.
    - Normalize to short, clean skill tokens only.
    - Do NOT include long phrases or sentences. No extra wording.
    - Make sure that you give me the complete "preferred" and "desired" set of skills
    - Capture all the important skills too, not just "Communications", focus on variety of techincal skills from the domains, there are usually a decent number of them, not just a few words it should be a short paragrpah sized, but again as a condensed list.

  (b) Years of Experience (YOE)
    - Merge required and preferred experience into a single concise representation.
    - Interpret ranges, "+" requirements, and labels like "entry-level/new grad/internship".
    - Examples: "2-4 years", "3+ years", "0-1 years (entry level)", "0 years (internship)".

  (c) Degree Requirements
    - Merge required and preferred (nice-to-have) degrees into one summary.
    - Normalize into concise labels such as:
      "Bachelor's", "Master's", "PhD", "Associate", "High School",
      or clear combinations when implied.
    - Avoid verbose sentences; keep it short and structured.

- Write the inferred values into:
    • G: Degree
    • H: YOE
    • I: Skills (comma-separated, condensed list - short paragraph sized around 4 sentences.)

3. Output format for extract_structured_fields
- When extract_structured_fields is called, its return value must ONLY contain lines in this exact textual shape
  (no JSON, no extra commentary, no additional text):

    Degree: <inferred degree summary>
    YOE: <inferred years-of-experience summary>
    Skills from the description: <comma-separated condensed skills>

- Repeat this 3-line block for each updated row, separated by a single blank line.
""".strip()

description_agent = Agent(
    model=MODEL,
    name="job_description_backfill_agent",
    description=backfill_agent_instruction,
    tools=[enrich_job_search_database],   # <-- single tool that does both
    generate_content_config=types.GenerateContentConfig(temperature=0),
    output_key="matching_data",
)

__all__ = ["description_agent", "backfill_job_descriptions", "extract_structured_fields"]