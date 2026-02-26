FROM python:3.13-slim

WORKDIR /app

# Avoid python writing .pyc files + keep logs immediate
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Fly expects your app to listen on this internal port
ENV PORT=8080

CMD ["sh", "-c", "gunicorn -k uvicorn.workers.UvicornWorker app:app --bind 0.0.0.0:${PORT} --log-level=info --access-logfile=- --error-logfile=-"]
