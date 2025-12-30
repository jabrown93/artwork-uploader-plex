FROM dhi.io/python:3.14.2

WORKDIR /app

# Copy requirements first for layer caching
COPY requirements.txt .

# Install dependencies (this layer is cached)
RUN pip install --no-cache-dir -r requirements.txt

# Copy only runtime code from src/
COPY src/ /app/src/

# Set Python path to find modules in src/
ENV PYTHONPATH=/app/src:$PYTHONPATH

# Create directories for volume mounts
RUN mkdir -p /bulk_imports /config /logs

EXPOSE 4567

# Entry point now in src/
ENTRYPOINT ["python", "/app/src/artwork_uploader.py"]

CMD ["--debug"]
