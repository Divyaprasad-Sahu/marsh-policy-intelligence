FROM node:20-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY pptx_engine/package.json pptx_engine/pnpm-lock.yaml ./pptx_engine/
RUN corepack enable \
    && cd pptx_engine \
    && pnpm install --frozen-lockfile --prod

COPY app ./app
COPY api ./api
COPY pptx_engine/render.mjs ./pptx_engine/render.mjs
COPY data/policies ./data/policies

RUN mkdir -p /var/data \
    && chown -R node:node /app /var/data

USER node

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]

