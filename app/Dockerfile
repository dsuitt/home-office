FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen
COPY money ./money
ENV MONEY_DB=/data/money.db
VOLUME /data
EXPOSE 8000
CMD ["uv", "run", "--no-dev", "uvicorn", "money.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
