# Nov 5:

- <span style="color:red">Steven</span> ★ make agent that connects to linkedin, glassdoor, Indeed and looks for recent (posted within 7 days) in agentic way 
    - get the todays day and time
    - fix the job searching 
        - for prompts without the specification of the company ( aka find me 50 jobs with this title or similar and this year of experience )
        - make sure the years of experience are enforced
        - and for prompts where company is specified make sure it works too
        - make sure that the title, company, location, date posted, description, id and link are available for each job. job descsription should not be trunckated 
- <span style="color:red">Steven</span>  ★ connect to apollo io/contactout/rocket agent that can scrape info of recruiters, and managers, and reach out to them
- ✅  <span style="color:red">Steven</span> check that the time is properly retrieved in each agent
    - run the code in chat and make sure that each agent has a check what current time function and is using it in the code
- <span style="color:red">Grant</span> [easy]  cutomize resume for specific job using latex (Grant)

-  ✅ <span style="color:green">Grant</span> ★ truly web search agent (for resume customization, reasoning capabilities with access to web)
-  ✅ <span style="color:green">Steven</span> [easy] google drive agent
-  ✅ <span style="color:green">Steven</span> [easy] fix gmail agent so it can send emails and upload the pdf from google drive to the email


# nov 6:
- ★ cold calling agent, cold email agent (make sure that we can add voicemail because most of recruiters won't pick up the phone)
- ★ start ui and cloud run  
    - for ui :  
        - upload all relevant files to google drive 
        - list relevant github repos ()
        - ★ ★ ★ ★ ★ ★ ★  authenticaltion of google services autimatically 


Next steps:
## more important tasks:
stage 0:
- google for jobs agent 
- gets data, populates excel sheet
- 
---

state 1:  is that data is populated in excel sheet

google sheets agent
- reads the data 
- generates the custom emails 
- pushes customizaiton platform for custom resume agent and saves the pdf back to the field

custom resume agent:
- displays the resume on the left job description of the right, displays keywords and synonyms
- has clickable areas where user can add comments for genimi to processs
- has 2 buttoms: update resume and Done
- it can be iterative, so we can update it multiple times
- it has acesss to projects, google docs, maybe slides, and potentially links to github
- saves

---
state 2: automate reaching out to people 
- finding jobs and recrutiers 
    - apollo mcp like thing
    - contactout and other platforms 
    
- populate recruiters to excel
- if all required fields are there -> email and or call
- emailing can be done with google agent
    - paste text from excel table
    - attach resume from excel
- calling will be done with twillio or google call agent (idk if it exists)
- attaching availability with link for my availability and scheduling the meeting similar to calendly - calendar agent

---
stage 3:
- make the ui (firebase)
- stats to show how many people reached out to, how many people responded, how many resumes customized, how much time saved
- if we can hool up commet with out tool, then we can potentially apply automatically with custom resume
- demo
---
stage 4:
if someone got interested in us, we can schedule a prep for them in the calendar and pull useful links/ resources with reasonable time estimaes 

## G suite
gmail:
- sending functionality 
- attaching files and sending them as well as being able to understand received files
- searching for a similar sender / title of email / (maybe) content

gcal:
- it is not giving ranges for events
- not always reports the events if there are overlapped 

google docs
- TBD

google sheets
- TBD

---




# sync deps 
```bash
uv sync
```
# run project 
```bash
uv run -- adk web 
# or if using .venv just 
source .venv/bin/activate 
adk web
```
# how to add packages
```bash
uv add <packageName>
uv lock
uv sync
```
# run .creds/verify.py for the first time to generate token
```bash
uv run .creds/verify.py
```
---
# current tree:
```
personalPlanner
├── .creds
│   ├── credentials.json
│   ├── token.json              
│   └── verify.py
├── calendarAgent
│   └── agent.py
├── .env
├── pyproject.toml
├── README.md
└── uv.lock
```
run adk web from personalPlanner directory
--- 
turn on Vertex and auth (ADC)
```bash
gcloud services enable aiplatform.googleapis.com
gcloud auth application-default login
```
your .env should look like this:
```
GOOGLE_GENAI_USE_VERTEXAI=True
GOOGLE_CLOUD_PROJECT=!!!!!!!!!!!!!!your project id from gcp!!!!!!!!!!!!!!!!!
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_OAUTH_CLIENT_FILE=.creds/credentials.json
GOOGLE_OAUTH_TOKEN_FILE=.creds/token.json
```

