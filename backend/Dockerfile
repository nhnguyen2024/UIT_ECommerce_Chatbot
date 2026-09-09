# Build stage: resolve and install dependencies into a virtualenv we can copy.
# Keeping the build tooling out of the final image cuts its size and its attack
# surface, since pip and setuptools never ship to production.
FROM python:3.12-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /src

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependencies are installed from pyproject before the source is copied, so a
# code change does not invalidate the dependency layer.
COPY pyproject.toml ./
RUN python - <<'PY' > /tmp/requirements.txt
import tomllib
with open("pyproject.toml", "rb") as handle:
    print("\n".join(tomllib.load(handle)["project"]["dependencies"]))
PY
RUN pip install -r /tmp/requirements.txt

COPY app ./app
COPY seed ./seed

# --no-deps because the dependency layer above already resolved everything.
# This step only installs our own package, including the policy corpus and the
# system prompt declared as package data in pyproject.toml.
RUN pip install --no-deps .


# Runtime stage.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Run as an unprivileged user. Azure Container Apps does not require it, but a
# compromised process should not be root.
RUN useradd --create-home --uid 10001 appuser

COPY --from=build /opt/venv /opt/venv

# A neutral working directory on purpose. Nothing of ours lives here, so the
# application always resolves to the installed package rather than to a stray
# source tree that happens to be on the path.
WORKDIR /home/appuser
USER appuser

EXPOSE 8000

# Azure Container Apps injects PORT. Default to 8000 for local runs.
ENV PORT=8000

# Single worker: the container scales out by replica, not by process, and each
# extra worker would hold its own MongoDB connection pool for no gain.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
