# ********** Stage 1: Builder stage ***********
# Purpose: Build and install Python dependencies in an isolated environment
FROM python:3.12-slim AS builder

# set the working directory inside the container to /app
WORKDIR /app

# Install the necessary system dependencies for building Python packages
# - build-essential: Provides essential compilation tools (gcc, make, etc.)
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    musl-dev \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
    libfreetype6-dev \
    libssl-dev \
    libffi-dev

# copy the dependency list into the container
# This allows pip to install the required packages without copying the entire source code    
COPY pyproject.toml README.md poetry.lock ./

# copy the source code into the container
COPY src/ ./src

# Install Python dependencies and build wheels
RUN pip install --upgrade pip setuptools wheel \
    && pip wheel --no-build-isolation --wheel-dir /app/wheels .


# *********** Final Stage: Runtime stage ***********
# Purpose: Create a minimal runtime image with only the necessary dependencies and application code
FROM python:3.12-slim

# set the working directory inside the container to /app
WORKDIR /app

# Create a dedicated non-root user for running the application
# Running as non-root improves container security by limiting privileges
RUN groupadd -r usergroup && useradd -r -g usergroup user

# Copy installed dependencies and application code from builder stage
# This avoids reinstalling dependencies in the final image, saving time and space
COPY --from=builder /app/wheels /wheels

# Install the dependencies from the wheels directory and clean up to reduce image size
RUN pip install --no-cache-dir --upgrade /wheels/* \
    && rm -rf /wheels

COPY run.py ./

# Switch to non-root user for all subsequent operations
USER user

# Environment variable to ensure Python can locate the application modules
ENV PYTHONPATH=/app/src

# Expose application port (5000) for external access
EXPOSE 5000

# Define a health check to monitor container availability
# - Runs every 30s, times out after 10s
# - Retries 3 times before marking container unhealthy
# - Uses Python socket to verify the app is listening on port 5000
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import socket; s=socket.socket(); s.connect(('localhost',5000))"

# Default command: start the application
# Using explicit Python invocation ensures consistent entrypoint behavior
CMD ["python", "run.py"]