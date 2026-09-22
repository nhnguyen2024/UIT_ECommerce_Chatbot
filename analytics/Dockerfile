# The analytics loop as a scheduled job: extract from the app, upload to the
# lake, load and refresh in Snowflake, write gold back to the app.
# Runs as an Azure Container Apps Job (infra/deploy-analytics.sh). No secrets in
# the image: they arrive as environment variables from Key Vault at run time.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ANALYTICS_OUT=/tmp/analytics

WORKDIR /app
COPY requirements-job.txt .
RUN pip install --no-cache-dir -r requirements-job.txt

COPY pipeline.py .
COPY snowflake/ snowflake/

RUN useradd --uid 10001 --no-create-home appuser
USER appuser

CMD ["python", "pipeline.py", "scheduled"]
