FROM python:3.14.8@sha256:504679006065cdab9a85e983e269140d3c5a279492395a6863b25b1ffb377f86

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
