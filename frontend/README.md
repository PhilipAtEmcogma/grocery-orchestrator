# Smart Grocery Assistant — Frontend

React + Vite frontend for the Smart Grocery & Meal Budget Assistant.

## Features

- Chat interface for price checks and meal-plan requests.
- Event-driven rendering of:
  - Price comparisons with citations.
  - Meal plans with shopping lists.
  - Notices, `no_data`, and error states.
- Session continuity via `session_id`; idempotent retries via `turn_id`.

## Local development

```bash
npm install
npm run dev
```

Configure `.env.local`:

```env
VITE_API_URL=https://<api-id>.execute-api.ap-southeast-2.amazonaws.com/<stage>/chat
```

## Build and deploy

```bash
npm run build
aws s3 sync dist/ s3://ga-frontend-097087133897-ap-southeast-2-an --profile grocery-sandbox
aws cloudfront create-invalidation --distribution-id <CF_DIST_ID> --paths "/*" --profile grocery-sandbox
```

## Contract

Implements the frontend–orchestrator contract in `CONTRACT-v1.md`:
- Event-shaped responses.
- Citations for all monetary values.
- Structured `price_comparison` and `meal_plan` events.
