# syntax=docker/dockerfile:1.7
# One image for the API, the worker and the dashboard; compose picks the command.

FROM python:3.12-slim AS build
WORKDIR /src
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY pyproject.toml README.md requirements.txt ./
COPY growthops ./growthops
RUN python -m pip wheel --wheel-dir /wheels ".[rag]" "streamlit>=1.49,<2" "pandas>=2.2,<3"

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    GROWTHOPS_DATABASE=/data/growthops.db \
    GROWTHOPS_EMBEDDING_CACHE_DIR=/opt/growthops/models \
    GROWTHOPS_DOCS_DIR=/app/docs \
    HOME=/tmp
RUN groupadd --system --gid 10001 growthops \
 && useradd --system --uid 10001 --gid growthops --home-dir /app --shell /usr/sbin/nologin growthops
WORKDIR /app
COPY --from=build /wheels /wheels
RUN python -m pip install --no-index --find-links /wheels growthops-os onnxruntime tokenizers numpy streamlit pandas \
 && rm -rf /wheels
# The embedding model is fetched and SHA-256 verified at build time, so the running container needs no internet.
RUN python -m growthops.embeddings && chmod -R a+rX /opt/growthops
COPY --chown=growthops:growthops docs ./docs
COPY --chown=growthops:growthops streamlit_app.py ./
COPY --chown=growthops:growthops .streamlit ./.streamlit
RUN mkdir -p /data && chown growthops:growthops /data
USER growthops
VOLUME ["/data"]
EXPOSE 8000 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "uvicorn", "growthops.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
