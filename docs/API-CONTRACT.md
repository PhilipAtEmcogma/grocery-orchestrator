# Grocery Assistant API Contract

## 1. Overview

The API provides a conversational interface for grocery price comparisons
and budget-based meal planning.

## 2. Base URL

Development:

```text
To be provided after API Gateway deployment
```

Production:

```text
To be provided
```

## 3. Endpoint

### POST /chat

Processes a user message and returns a grounded response based on available
grocery price data.

## 4. Request headers

```http
Content-Type: application/json
```

## 5. Request body

```json
{
  "message": "What's the cheapest butter near me?",
  "session_id": "demo-session-001",
  "location": "Auckland"
}
```

### Request fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `message` | string | Yes | User's grocery or meal-planning request |
| `session_id` | string | Yes | Identifier for maintaining conversation context |
| `location` | string | Yes | User's location or supported store area |

## 6. Price comparison response

```json
{
  "type": "price_comparison",
  "message": "The cheapest butter is available at Pak'n Save.",
  "items": [
    {
      "name": "Butter",
      "brand": "Example Brand",
      "size": "500g",
      "store": "Pak'n Save",
      "location": "Auckland",
      "price": 3.49,
      "currency": "NZD",
      "availability": "in_stock",
      "price_date": "2026-08-19",
      "citation": {
        "source": "Pak'n Save",
        "date": "2026-08-19"
      }
    }
  ]
}
```

## 7. Meal plan response

```json
{
  "type": "meal_plan",
  "message": "Here is a meal plan for three people under $30.",
  "budget": 30.00,
  "currency": "NZD",
  "household_size": 3,
  "meals": [
    {
      "name": "Vegetable Pasta",
      "ingredients": [
        {
          "item": "Pasta",
          "quantity": "500g",
          "store": "Pak'n Save",
          "price": 2.00,
          "currency": "NZD",
          "availability": "in_stock",
          "citation": {
            "source": "Pak'n Save",
            "date": "2026-08-19"
          }
        }
      ],
      "total": 2.00
    }
  ],
  "grand_total": 2.00,
  "remaining_budget": 28.00
}
```

## 8. General response

```json
{
  "type": "general",
  "message": "I can help compare grocery prices and create meal plans."
}
```

## 9. Missing or unavailable products

```json
{
  "type": "price_comparison",
  "message": "I could not find current price data for this item.",
  "items": [],
  "missing_items": [
    {
      "name": "Example Product",
      "reason": "not_found"
    }
  ]
}
```

The frontend must not display a price unless it is included in the
authoritative response data.

## 10. Error responses

### Bad request — 400

```json
{
  "error": "invalid_request",
  "message": "The message field is required."
}
```

### Internal server error — 500

```json
{
  "error": "internal_error",
  "message": "The request could not be completed."
}
```

## 11. Frontend integration notes

The frontend must:

- Send a unique `session_id` with each request.
- Send the user's `location`.
- Display prices only from returned structured data.
- Display the store and date for each citation.
- Display meal ingredients, per-meal totals, and the grand total.
- Handle unavailable products without crashing.
- Display a useful error message when the API is unavailable.

## 12. Pending decisions

- Confirm the exact Lambda request format.
- Confirm the exact Lambda response format.
- Confirm whether `location` is free text or a fixed list.
- Confirm whether prices are numeric values or formatted strings.
- Confirm the final API Gateway URL.
- Confirm authentication requirements.
