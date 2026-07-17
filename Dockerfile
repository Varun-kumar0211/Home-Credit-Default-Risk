FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libgomp1 libomp-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 procps curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=base /usr/local /usr/local
COPY . .

WORKDIR /app/Backend
EXPOSE 7860 8000

COPY entrypoint.sh /app/Backend/entrypoint.sh
RUN chmod +x /app/Backend/entrypoint.sh
CMD ["/bin/sh", "-c", "/app/Backend/entrypoint.sh"]
