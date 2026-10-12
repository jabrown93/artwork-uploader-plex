FROM python:3.15.0@sha256:3c24e0b3ef4c5e2409f918894ccd5e05756eea2015ae6ddd289179b20a049f9b

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

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh", "python", "/app/src/artwork_uploader.py"]

CMD ["--debug"]
