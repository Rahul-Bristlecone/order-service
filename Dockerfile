# -------------------------------
# Stage 1: Builder stage
# Purpose: Build and install Python dependencies in an isolated environment
# -------------------------------
FROM python:3.12-slim AS builder
WORKDIR /app

# Upgrade pip, setuptools, and wheel to latest versions
# Ensures compatibility with modern packages and avoids installation issues
RUN pip install --upgrade pip setuptools wheel

# Copy dependency list into container and install them
# --no-cache-dir prevents caching wheels, reducing image size
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code into builder stage
# This makes local modules available during dependency resolution
COPY . ./order_service


# -------------------------------
# Stage 2: Final stage - Runtime environment
# Purpose: Create a minimal, secure runtime image with only necessary artifacts
# -------------------------------
FROM python:3.12-slim
WORKDIR /app

# Create a dedicated non-root user for running the application
# Running as non-root improves container security by limiting privileges
RUN groupadd -r usergroup && useradd -r -g usergroup user

# Copy installed dependencies and application code from builder stage
# This avoids reinstalling dependencies in the final image, saving time and space
COPY --from=builder /usr/local /usr/local
COPY --from=builder /app /app

# Adjust ownership of application files so the non-root user can access them
RUN chown -R user:usergroup /app

# Set environment variables:
# - PATH ensures Python binaries are accessible
# - PYTHONPATH allows imports from /app without modifying sys.path in code
ENV PATH="/usr/local/bin:$PATH"
ENV PYTHONPATH=/app

# Switch to non-root user for all subsequent operations
USER user

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
CMD ["python", "order_service/run.py"]