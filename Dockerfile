# Use Python 3.11 as base image
FROM docker.cnb.cool/masx200/docker_mirror/mcpo-docker:2025-08-23-17-10-51
copy ./sources.list /etc/apt/sources.list

copy ./debian.sources /etc/apt/sources.list.d/debian.sources
# Install Node.js 22.x
RUN apt-get update && apt-get install -y curl gnupg && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && apt-get install -y nodejs sudo nano && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
run sudo apt install apt-transport-https ca-certificates curl && apt-get clean 
# Install uv
RUN pip install uv

RUN uv venv
RUN uv pip install mcpo
# Install mcpo-control-panel using uv
RUN uv pip install mcpo-control-panel

# Set working directory
WORKDIR /app

# Expose the port (default is 8083, can be overridden by MCPO_MANAGER_PORT)
EXPOSE 8083
EXPOSE 8000

# Set environment variables with defaults
ENV MCPO_MANAGER_HOST=0.0.0.0
ENV MCPO_MANAGER_PORT=8083
ENV MCPO_MANAGER_DATA_DIR=/data

# Create data directory
RUN mkdir -p /data

# Command to run the application
# The application will use the environment variables for host, port, and config directory
CMD ["uv", "run", "python", "-m", "mcpo_control_panel", "--host", "${MCPO_MANAGER_HOST}", "--port", "${MCPO_MANAGER_PORT}", "--config-dir", "${MCPO_MANAGER_DATA_DIR}"]
