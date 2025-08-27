# Use an official Python runtime as a parent image
FROM python:3.11-slim-bookworm

# Install gcsfuse dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Add the gcloud-sdk repository

# Set the working directory in the container
WORKDIR /app

# Copy the dependencies file to the working directory
COPY requirements.txt .
ENV PATH="/usr/local/go/bin:${PATH}"

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the content of the local src directory to the working directory
COPY benchmark.py .

# Specify the command to run on container start
ENTRYPOINT ["python3", "benchmark.py"]
