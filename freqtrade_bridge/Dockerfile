# Freqtrade-Lean Bridge Docker image
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bridge modules
COPY freqtrade_bridge/ ./freqtrade_bridge/

# Install Freqtrade client
RUN pip install --no-cache-dir freqtrade-rest-client

EXPOSE 8080 9090

CMD ["python", "-m", "freqtrade_bridge"]