# Use Python 3.12 base image
FROM python:3.12-slim

# Where in the container your app will live
WORKDIR /app

# Optional: some sane defaults
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies (only if you need build tools; safe for now)
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# Make sure requirements.txt is in your project root before build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your code into the container
COPY . .

# Cloud Run sets PORT dynamically; default to 8080 for local
ENV PORT=8080

# Start the FastAPI server using uvicorn
# server:app  -> server.py file, app = FastAPI() instance
CMD exec uvicorn server:app --host 0.0.0.0 --port $PORT