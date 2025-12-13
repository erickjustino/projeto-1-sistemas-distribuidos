FROM python:3.10-slim

# Mantém o Python rápido no gatilho (sem buffer)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 80

# VOLTAMOS COM O SILENCIADOR DO SERVIDOR WEB
# --log-level warning: Só avisa se der erro grave
# --no-access-log: Não mostra os "200 OK"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80", "--log-level", "warning", "--no-access-log"]