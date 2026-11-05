FROM python:3.15.2@sha256:ca485a0696c0baeb0a11a6bf103bec17b8f7d9e36d51b9c5d1ddf6ad927302b6

ENV PATH="/app/venv/bin:$PATH"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
ENV PATH="/app/venv/bin:${PATH}"
ENV PYTHONPATH="/app/src:${PYTHONPATH}"

COPY requirements.txt .

# Install tini for signal forwarding and gosu for dropping privileges
RUN apt-get update && \
    apt-get install -y --no-install-recommends gosu tini && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    python -m venv /app/venv && \
    pip install --no-cache-dir -r requirements.txt && \
    groupadd -g 1027 artwork && \
    useradd -u 1027 -g artwork -m artwork

# Copy source last so editing it doesn't bust the dependency-install layer above
COPY src/ /app/src/

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Expose web UI port
EXPOSE 4567

# Health check configuration
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; exit(0 if urllib.request.urlopen('http://localhost:4567/api/health').getcode() == 200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh", "python", "/app/src/artwork_uploader.py"]

CMD ["--debug"]
