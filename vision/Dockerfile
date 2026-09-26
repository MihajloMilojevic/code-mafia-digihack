FROM python:3.11-slim
WORKDIR /app

# opencv treba libGL/glib cak i za headless upotrebu
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python", "hand_tracker.py", "--no-preview"]
