FROM python:3.11 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv pip install -r pyproject.toml --system --no-cache
RUN pip install --no-cache-dir git+https://github.com/yym68686/aient.git
RUN pip install --no-cache-dir git+https://github.com/yym68686/md2tgmd.git

FROM python:3.11-slim-bookworm
EXPOSE 8000
WORKDIR /home
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY . .
ENTRYPOINT ["python", "-u", "/home/bot.py"]