FROM python:3.10-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py wsgi.py ./
COPY templates ./templates
COPY static ./static

EXPOSE 5000

CMD ["waitress-serve", "--listen=0.0.0.0:5000", "wsgi:application"]
