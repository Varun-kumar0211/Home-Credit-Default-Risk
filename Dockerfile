FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

WORKDIR /app/Backend

ENV PORT=7860
EXPOSE 7860 8000

CMD ["python", "app.py"]
