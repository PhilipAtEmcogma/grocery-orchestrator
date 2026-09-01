# Grocery Orchestrator frontend

This app is a local React/Vite client for calling the backend `POST /chat` API.

## Prerequisites

- Node.js 20+
- Backend dev server running at `http://localhost:8000` (start from repo root with
  `python scripts/dev_server.py`)

## Run locally

From `/home/runner/work/grocery-orchestrator/grocery-orchestrator/frontend`:

```bash
npm install
npm run dev
```

The Vite dev server starts on `http://localhost:5173`.

## Configuration

Set `VITE_API_URL` to the backend chat endpoint base path.

- Default used by this app: `http://localhost:8000`
- Request target: `${VITE_API_URL}/chat` (for local dev this resolves to
  `http://localhost:8000/chat`)

Example:

```bash
VITE_API_URL=http://localhost:8000 npm run dev
```
