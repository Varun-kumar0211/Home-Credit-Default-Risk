# Home Credit Default Risk

This project contains a Gradio UI and a FastAPI API for credit default prediction.

## Run with Docker Compose
From the project root:

```bash
docker compose up --build
```

Open:
- Gradio UI: http://localhost:7860
- FastAPI docs: http://localhost:8000/docs

## Run manually

Start the API:

```bash
cd Backend
uvicorn main:app --host 0.0.0.0 --port 8000
```

In another terminal, start the UI:

```bash
cd Backend
python app.py
```
