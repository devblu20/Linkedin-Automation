FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
ENV DESKTOP_WORKER_DOWNLOAD=/app/downloads/BluQQ-Worker.zip
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY alembic.ini ./
COPY migrations ./migrations
COPY downloads ./downloads
RUN pip install .
EXPOSE 8000
CMD ["python", "-m", "linkedin_automation.interfaces.hosted_site"]
