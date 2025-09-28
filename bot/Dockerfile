FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update -y && apt-get install -y --no-install-recommends \
    iproute2 iputils-ping net-tools procps curl \
    gnuplot docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

VOLUME ["/app/data"]

CMD ["/app/entrypoint.sh"]


