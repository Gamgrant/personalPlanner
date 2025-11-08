# -------------------------------
# Base image
# -------------------------------
# Use a small Python image that still has everything we need
FROM python:3.11-slim

# -------------------------------
# Working directory
# -------------------------------
WORKDIR /app

# -------------------------------
# Python dependencies
# -------------------------------
# Install dependencies first so Docker can cache this layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# -------------------------------
# Non-root user (best practice)
# -------------------------------
RUN adduser --disabled-password --gecos "" myuser && \
    chown -R myuser:myuser /app

# -------------------------------
# Copy application code
# -------------------------------
# This copies:
#   - orchestrator/, calendar_service/, gmail_service/, etc.
#   - utils/
#   - .creds/  (credentials.json, token.json, .env)
#   - main.py
COPY . .

USER myuser

# -------------------------------
# Environment
# -------------------------------
# Cloud Run sets $PORT automatically; default to 8080 for local dev
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# If you want, you can hard-set defaults here (optional),
# but your .creds/.env is already being loaded by your code:
# ENV GOOGLE_GENAI_USE_VERTEXAI=True
# ENV GOOGLE_CLOUD_LOCATION=us-central1
# ENV GOOGLE_CLOUD_PROJECT=personalplanner-476218

# -------------------------------
# Start FastAPI via Uvicorn
# -------------------------------
# main:app → main.py file, `app` FastAPI instance
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT"]
