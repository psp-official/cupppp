FROM python:3.11-slim

# Render Server ပေါ်တွင် FFmpeg သွင်းခြင်း
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render မှ ပေးသော PORT ဖြင့် Run ခြင်း
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
