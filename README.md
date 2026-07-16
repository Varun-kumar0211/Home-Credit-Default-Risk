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

## Deploy on Hugging Face Spaces with Docker

This project can run in a single Hugging Face Space using the root `Dockerfile`.
The container starts both services together:
- `uvicorn Backend.main:app --host 0.0.0.0 --port 8000`
- `python Backend/app.py`

On Spaces, the Gradio UI is exposed on the provided `PORT` environment variable, and the frontend calls the local FastAPI backend at `http://127.0.0.1:8000`.

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
