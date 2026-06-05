FROM python:3.11 AS builder

RUN apt-get update && apt-get install -y git

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv pip install -r pyproject.toml --system --no-cache

# Clone the submodules directly to copy them to the final stage
RUN git clone https://github.com/yym68686/aient.git /app/aient
RUN git clone https://github.com/yym68686/md2tgmd.git /app/md2tgmd

FROM python:3.11-slim-bookworm
EXPOSE 8000
WORKDIR /home
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY . .
# Copy the cloned repositories to resolve the local imports structure
COPY --from=builder /app/aient /home/aient
COPY --from=builder /app/md2tgmd /home/md2tgmd

ENTRYPOINT ["python", "-u", "/home/bot.py"]