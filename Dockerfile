FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY growthops ./growthops
RUN python -m pip install --no-cache-dir .

ENV GROWTHOPS_DATABASE=/app/data/growthops-sample.db
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "growthops.api:app", "--host", "0.0.0.0", "--port", "8000"]
