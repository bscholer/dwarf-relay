FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY dwarf_relay ./dwarf_relay
RUN pip install --no-cache-dir .
ENTRYPOINT ["dwarf-relay"]
CMD ["proxy", "--log-dir", "/data"]
