# Use a lightweight Python base image
FROM python:3.10-slim

# Set the working directory inside the container
WORKDIR /app

# Install necessary system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python libraries
RUN pip install -U "jax[k8s,tpu]" && \
    pip install --no-cache-dir \
    jax[k8] \
    orbax-checkpoint

# Copy the script to the container
COPY script.py .

# Set the default command to execute the script
ENTRYPOINT ["python3", "script.py"]
