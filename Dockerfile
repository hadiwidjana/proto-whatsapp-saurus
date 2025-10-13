FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY pyproject.toml .

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Copy application code
COPY . .

# Create logs and pids directories
RUN mkdir -p logs pids

# Expose port
EXPOSE 5000

# Default command (can be overridden)
CMD ["python", "main.py"]
