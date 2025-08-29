FROM python:3.11-slim-bookworm

WORKDIR /app

# Copy the dependencies file to the working directory
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY benchmark.py .

ENTRYPOINT ["python3", "benchmark.py"]
