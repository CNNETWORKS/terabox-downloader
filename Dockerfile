FROM python:3.11-slim

WORKDIR /app

# Install system deps (ffmpeg useful for video operations)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Use non-root user
RUN useradd -m botuser || true
USER botuser

ENV PYTHONUNBUFFERED=1
CMD ["python", "bot.py"]
