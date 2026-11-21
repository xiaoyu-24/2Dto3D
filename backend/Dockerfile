FROM python:3.13-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
COPY requirements* ./
RUN pip install --no-cache-dir -r requirements-test.txt -c requirements.lock
COPY . .
USER 65534:65534
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
