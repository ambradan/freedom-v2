FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client curl git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY config ./config
COPY PROFILE.md .
ENV PYTHONPATH=/app/src
CMD ["python", "-m", "freedom.main"]
