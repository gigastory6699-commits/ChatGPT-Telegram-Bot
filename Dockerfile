FROM python:3.11 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv pip install -r pyproject.toml --system --no-cache

FROM python:3.11-slim-bookworm
EXPOSE 8000

# Install Node.js
RUN apt-get update && apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /home
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY . .

# Install whatsapp bridge packages
RUN cd whatsapp_bridge && npm install --omit=dev

RUN chmod +x start.sh
ENTRYPOINT ["/bin/sh", "start.sh"]