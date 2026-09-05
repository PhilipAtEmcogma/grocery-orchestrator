# Smart Grocery Assistant - Data Schema & Handover Specification

This document provides the complete data architecture, DynamoDB schema field descriptions, 10-store Auckland supermarket dataset specifications (August 28, 2026 snapshot), 5 interactive frontend chat scenario prompts with ideal response payloads, and 2 star demonstration recipes with 100% ingredient match for the **Smart Grocery & Meal Budget Assistant**.

> [!NOTE]
> **Data Snapshot Notice**: All supermarket product pricing and inventory data in this dataset was collected on **August 28, 2026** across 10 physical store locations in Auckland. It serves as a representative, realistic benchmark snapshot for AI evaluation, local development, and offline demonstration (not real-time live sync).

---

## 1. Multi-Store Dataset Overview (10 Auckland Stores - Aug 28, 2026 Snapshot)

The dataset contains **3,000 supermarket products** (300 products per store) across 10 physical locations paired 1:1 across 5 major geographic zones of Auckland, captured on August 28, 2026, plus **175 curated recipes** from TheMealDB:

| Auckland Region | PAK'nSAVE Store | New World Store | Store UUID (PAK'nSAVE / New World) |
| :--- | :--- | :--- | :--- |
| **Central Auckland** | PAK'nSAVE Mt Albert | New World Newmarket | `b2e98a14-c8ca-401e-99ed-edf74570c6f6` / `51ee2a79-4693-410c-9287-66940847736d` |
| **North Shore** | PAK'nSAVE Albany | New World Albany | `65defcf2-bc15-490e-a84f-1f13b769cd22` / `773ad0a0-024e-46c5-a94b-df1cf86d25cc` |
| **West Auckland** | PAK'nSAVE Lincoln Road | New World New Lynn | `92086ded-a55d-4241-a364-7d7ea91531b4` / `c8998066-d39b-401c-aa6b-d6d18f8d122f` |
| **East Auckland** | PAK'nSAVE Sylvia Park | New World Remuera | `c0f80e87-16be-4488-9553-da437e8c6c2a` / `91e4dddd-aed7-4ccc-ad0b-dfe25805a495` |
| **South Auckland** | PAK'nSAVE Manukau | New World Papakura | `9cd8eb60-3222-4efc-bd7c-50e03e6a81a4` / `ef977d89-f3d8-4e8b-8a48-b895ded38646` |

### Data Sources & Provenance:
- **Supermarket Product Prices**: Sourced from Foodstuffs online shopping catalog across 10 Auckland physical store locations (Captured: **28 August 2026**). Cleaned, deduplicated, and enriched with standardized price and category tags.
- **Recipe Data**: Curated from [TheMealDB Public API](https://www.themealdb.com/api.php) ([Terms](https://www.themealdb.com/terms_of_use.php)), featuring Asian and British regional dishes with detailed ingredient lists, preparation steps, and image URLs.

### Available Processed Data Files:
- **PAK'nSAVE Consolidated CSV (1,500 items)**: `data/processed/pakn_save_products_latest.csv`
- **PAK'nSAVE Consolidated JSON (1,500 items)**: `data/processed/pakn_save_products_latest.json`
- **New World Consolidated CSV (1,500 items)**: `data/processed/new_world_products_latest.csv`
- **New World Consolidated JSON (1,500 items)**: `data/processed/new_world_products_latest.json`
- **Curated Recipes (175 recipes)**: `data/processed/recipes_latest.csv`, `data/processed/recipes_latest.json`
- **DynamoDB Store JSONs (10 files)**: `data/dynamodb_products/*_items.json`
- **DynamoDB Recipe Batches (7 files)**: `data/dynamodb_recipe_batches/recipes_batch_*.json`

---

## 2. DynamoDB Table Schemas & Metadata

### 2.1 Table: `smart-grocery-products-dev`

The Products table stores August 28, 2026 grocery items from PAK'nSAVE and New World, serving as the grounding source of truth for all price and multi-store comparison queries.

- **Primary Key (PK)**: `primary_key` (String, HASH)
- **Billing Mode**: `PAY_PER_REQUEST` (On-Demand)
- **Global Secondary Index (GSI)**:
  - `CategoryPriceIndex`: Partition Key `category` (String), Sort Key `price` (Number, ascending for instant lowest price lookup).

| Field Name | Type | Key Type | Field Description |
| :--- | :--- | :--- | :--- |
| `primary_key` | String | **Partition Key (PK)** | Unique DynamoDB primary key formatted as `{chain_slug}_{store_slug}_{product_id}` (e.g. `pakn_save_albany_5201479-EA-000`). Prevents key collisions when the same product exists in multiple stores. |
| `store_id` | String | Attribute | Official Foodstuffs retailer UUID identifying the physical store location. |
| `store_name` | String | Attribute | Human-readable store display name (e.g. `PAK'nSAVE Mt Albert`, `New World Newmarket`). |
| `product_id` | String | Attribute | Original retailer SKU / stock identifier (e.g. `5201479-EA-000`). |
| `product_name` | String | Attribute | Cleaned product display title (e.g. `Standard Milk`, `Free Range Eggs 10pk`). |
| `brand` | String or NULL | Attribute | Manufacturer / brand name (e.g. `Pams`, `Meadow Fresh`, `Tegel`). May be `NULL` for loose produce. |
| `size` | String | Attribute | Package selling unit or volume description (e.g. `2l`, `1kg`, `500g`, `12pk`, `ea`). |
| `price` | Number | **GSI Sort Key** | Retail selling price in New Zealand Dollars (NZD), e.g. `4.79` (as of Aug 28, 2026). |
| `category` | String | **GSI Partition Key** | Standardized food taxonomy category (e.g. `Fresh Milk & Plant Milk`, `Chicken & Poultry`) used for CategoryPriceIndex lookup. |

---

### 2.2 Table: `smart-grocery-recipes-dev`

The Recipes table stores structured culinary data from TheMealDB. It is decoupled from supermarket product IDs to maintain data independence and support flexible ingredient matching.

- **Primary Key (PK)**: `recipe_id` (String, HASH)
- **Billing Mode**: `PAY_PER_REQUEST` (On-Demand)
- **Global Secondary Indexes (GSI)**:
  - `CategoryRecipeIndex`: Partition Key `category` (String), Sort Key `recipe_name` (String)
  - `AreaRecipeIndex`: Partition Key `area` (String), Sort Key `recipe_name` (String)

| Field Name | Type | Key Type | Field Description |
| :--- | :--- | :--- | :--- |
| `recipe_id` | String | **Partition Key (PK)** | Stable unique recipe identifier prefixed with provider (e.g. `mealdb#52893`). |
| `recipe_name` | String | **GSI Sort Key** | Cleaned ASCII-safe title of the recipe (e.g. `Chicken Fried Rice`, `Beef Sunday Roast`). |
| `category` | String | **GSI Partition Key** | Primary meal category (e.g. `Chicken`, `Beef`, `Seafood`, `Vegetarian`, `Dessert`). |
| `area` | String | **GSI Partition Key** | Regional cuisine classification (e.g. `British`, `Chinese`, `Thai`, `Vietnamese`, `Malaysian`). |
| `country` | String | Attribute | Normalized country name associated with the culinary area. |
| `ingredients` | List of Maps | Attribute | Array of ingredient objects, each containing `name` (e.g. `Chicken Thighs`), `measure` (e.g. `500g`, `2 tbsp`), and `key` (normalized lookup token). |
| `ingredient_keys` | List of Strings | Attribute | Array of lowercase keyword tokens for fast text search and semantic matching. |
| `ingredient_count` | Number | Attribute | Total count of ingredients required for the dish. |
| `instructions` | String | Attribute | Complete step-by-step preparation and cooking instructions. |
| `thumbnail_url` | String | Attribute | Direct HTTPS link to the recipe's official dish photo. |
| `source_url` | String | Attribute | Original webpage link where the recipe was published (if available). |
| `youtube_url` | String | Attribute | Video tutorial link on YouTube (if available). |
| `mealdb_url` | String | Attribute | Official TheMealDB web page reference. |
| `attribution` | String | Attribute | Legal and source attribution text acknowledging TheMealDB public API. |
| `fetched_at` | String | Attribute | ISO-8601 UTC timestamp indicating when the recipe was fetched. |

---

## 3. Frontend Chat Demo Scenarios (5 Interactive Scenarios with Ideal Return Data)

These 5 scenarios provide test prompt sentences and the **ideal structured response data** (including store names, itemized shopping lists, individual prices, basket totals, and grounded natural language explanations) based on the August 28, 2026 dataset:

```
+-------------------------------------------------------------------------------+
|                      Frontend Chat Demo Scenarios                             |
+-------------------------------------------------------------------------------+
| 1. Location-Aware Price Check: "What is the cheapest 2L milk near Albany?"    |
| 2. Flatmate Budget Plan      : "Feed 3 flatmates for under $15 tonight."      |
| 3. Cross-Store Basket Total  : "Compare total cost for Beef Sunday Roast."    |
| 4. Healthy / High-Protein    : "High-protein dinner under $25 near Newmarket."|
| 5. Dietary Constraints       : "Dairy-free dinner for 2 with shopping list."  |
+-------------------------------------------------------------------------------+
```

---

### Scenario 1: Location-Aware Single Item Price Check

- **User Prompt**:
  > *"I live on the North Shore. What is the cheapest 2L milk near Albany right now?"*
- **Intent**: `single_item_price_check` (Filters: `region = North Shore`, `query = milk`)

#### Ideal Return Data:
```json
{
  "intent": "single_item_price_check",
  "category": "Fresh Milk & Plant Milk",
  "recommended_store": "PAK'nSAVE Albany",
  "recommended_item": {
    "product_name": "Standard Milk",
    "size": "2L",
    "price": 4.79,
    "store_name": "PAK'nSAVE Albany",
    "product_id": "5201479-EA-000"
  },
  "alternative_options": [
    { "product_name": "Lite Milk", "size": "2L", "price": 4.79, "store_name": "PAK'nSAVE Albany" },
    { "product_name": "Trim Milk", "size": "2L", "price": 4.79, "store_name": "PAK'nSAVE Albany" },
    { "product_name": "Standard Milk", "size": "2L", "price": 4.82, "store_name": "New World Albany" }
  ],
  "savings": 0.03,
  "comparison_summary": "PAK'nSAVE Albany ($4.79) vs New World Albany ($4.82)"
}
```

#### Grounded Assistant Response:
> "The cheapest 2L fresh milk in the Albany area (North Shore) is **Standard Milk** at **PAK'nSAVE Albany** for **\$4.79 (2L)**. For comparison, the exact same 2L milk is \$4.82 at **New World Albany**, saving you \$0.03. *(Data snapshot: Aug 28, 2026)*"

---

### Scenario 2: Budget Meal Plan for Students / Flatmates

- **User Prompt**:
  > *"We are 3 university flatmates with a strict budget. Can you recommend a dinner recipe we can make for under \$15 total, and tell us where to buy the ingredients cheapest in West Auckland?"*
- **Intent**: `budget_meal_plan` (Constraints: `budget <= $15`, `servings = 3`, `region = West Auckland`)

#### Ideal Return Data:
```json
{
  "intent": "budget_meal_plan",
  "recipe_id": "mealdb#52956",
  "recipe_name": "Chicken Fried Rice",
  "servings": 3,
  "store_name": "PAK'nSAVE Lincoln Road",
  "shopping_list": [
    { "item": "Boneless Skinless Chicken Breast", "size": "400g", "price": 8.99 },
    { "item": "SunRice Jasmine Rice", "size": "1kg", "price": 2.69 },
    { "item": "Fresh Carrots", "size": "1kg", "price": 1.99 }
  ],
  "total_price": 13.67,
  "cost_per_person": 4.56,
  "budget_limit": 15.00,
  "budget_remaining": 1.33
}
```

#### Grounded Assistant Response:
> "For 3 flatmates under \$15, I recommend **Chicken Fried Rice**. You can purchase the core fresh ingredients at **PAK'nSAVE Lincoln Road (West Auckland)**:
> - **Boneless Chicken Breast (400g)**: \$8.99
> - **Jasmine Rice (1kg)**: \$2.69
> - **Fresh Carrots (1kg)**: \$1.99
>
> **Total Basket Price**: **\$13.67** (approx. **\$4.56 per person**), successfully keeping you **\$1.33 under your \$15 budget**!"

---

### Scenario 3: Cross-Supermarket Recipe Basket Comparison

- **User Prompt**:
  > *"I want to cook a Beef Sunday Roast this weekend. Can you show me the full ingredient shopping list and compare the total basket price between PAK'nSAVE and New World in Central Auckland?"*
- **Intent**: `recipe_basket_comparison` (Target: `Beef Sunday Roast`, `region = Central Auckland`)

#### Ideal Return Data:
```json
{
  "intent": "recipe_basket_comparison",
  "recipe_name": "Beef Sunday Roast",
  "region": "Central Auckland",
  "store_1": {
    "store_name": "PAK'nSAVE Mt Albert",
    "items": [
      { "name": "NZ Beef Mince / Base", "size": "500g", "price": 8.99 },
      { "name": "White Washed Potatoes", "size": "1kg", "price": 2.99 },
      { "name": "Fresh Broccoli", "size": "1 head", "price": 2.49 },
      { "name": "Orange Carrots", "size": "1kg", "price": 1.99 },
      { "name": "Pure Plain Flour", "size": "1.5kg", "price": 1.89 },
      { "name": "Standard Milk", "size": "1L", "price": 1.49 }
    ],
    "total_price": 19.84
  },
  "store_2": {
    "store_name": "New World Newmarket",
    "items": [
      { "name": "Wagyu Beef Mince", "size": "500g", "price": 9.99 },
      { "name": "White Washed Potatoes", "size": "1kg", "price": 3.79 },
      { "name": "Fresh Broccoli", "size": "1 head", "price": 1.79 },
      { "name": "Orange Carrots", "size": "1kg", "price": 2.29 },
      { "name": "Pure Plain Flour", "size": "1.5kg", "price": 2.00 },
      { "name": "Standard Milk", "size": "1L", "price": 2.99 }
    ],
    "total_price": 22.85
  },
  "price_difference": 3.01,
  "percentage_savings": "13.2%",
  "cheapest_store": "PAK'nSAVE Mt Albert"
}
```

#### Grounded Assistant Response:
> "Here is your multi-store price breakdown for **Beef Sunday Roast** in Central Auckland:
> 
> | Ingredient | PAK'nSAVE Mt Albert | New World Newmarket |
> | :--- | :--- | :--- |
> | Beef Base / Mince (500g) | \$8.99 | \$9.99 |
> | White Potatoes (1kg) | \$2.99 | \$3.79 |
> | Fresh Broccoli (1 head) | \$2.49 | \$1.79 |
> | Carrots (1kg) | \$1.99 | \$2.29 |
> | Plain Flour (1.5kg) | \$1.89 | \$2.00 |
> | Fresh Milk (1L) | \$1.49 | \$2.99 |
> | **Total Basket** | **\$19.84** | **\$22.85** |
> 
> 🏆 **Cheapest Option**: **PAK'nSAVE Mt Albert** at **\$19.84**, saving you **\$3.01 (13.2%)** compared to New World Newmarket."

---

### Scenario 4: High-Protein Healthy Dinner with Location Filter

- **User Prompt**:
  > *"I'm looking for a healthy, high-protein seafood dinner recipe for 2 people under \$30. What can I make, and which supermarket near Newmarket has the ingredients?"*
- **Intent**: `nutritional_constraint_meal_plan` (Category: `Seafood`, `budget <= $30`, `region = Central Auckland`)

#### Ideal Return Data:
```json
{
  "intent": "nutritional_constraint_meal_plan",
  "recipe_id": "mealdb#52960",
  "recipe_name": "Baked Salmon with Greens",
  "nutrition_focus": "High-Protein & Low-Carb",
  "store_name": "New World Newmarket",
  "shopping_list": [
    { "item": "Sliced Smoked Salmon (100g x 2)", "price": 19.58 },
    { "item": "Fresh Broccoli (1 head)", "price": 1.79 },
    { "item": "Fresh Lemons (500g)", "price": 2.49 },
    { "item": "Loose Red Tomatoes (500g)", "price": 3.99 }
  ],
  "total_price": 27.85,
  "budget_limit": 30.00,
  "budget_remaining": 2.15
}
```

#### Grounded Assistant Response:
> "For a healthy, high-protein seafood dinner under \$30, I recommend **Baked Salmon with Greens**. You can purchase all ingredients at **New World Newmarket**:
> - **Salmon (100g x 2)**: \$19.58
> - **Fresh Broccoli (1 head)**: \$1.79
> - **Fresh Lemons (500g)**: \$2.49
> - **Red Tomatoes (500g)**: \$3.99
>
> **Total Basket Price**: **\$27.85** for 2 people (within your \$30 budget by \$2.15)."

---

### Scenario 5: Dietary Constraint with Ingredient Substitution

- **User Prompt**:
  > *"Plan a quick dinner for 2 people that is completely dairy-free. Show me the recipe and a shopping list with exact prices from PAK'nSAVE Sylvia Park."*
- **Intent**: `dietary_constraint_plan` (Constraint: `dairy_free`, Store: `PAK'nSAVE Sylvia Park`)

#### Ideal Return Data:
```json
{
  "intent": "dietary_constraint_plan",
  "recipe_id": "mealdb#52814",
  "recipe_name": "Thai Green Curry with Jasmine Rice",
  "dietary_compliance": "100% Dairy-Free",
  "store_name": "PAK'nSAVE Sylvia Park",
  "shopping_list": [
    { "item": "Skinless Chicken Breast (500g)", "price": 6.99 },
    { "item": "Canned Coconut Milk (400ml, Plant-based)", "price": 1.49 },
    { "item": "SunRice Jasmine Rice (1kg)", "price": 2.69 },
    { "item": "White Washed Potatoes (1kg)", "price": 3.29 },
    { "item": "Fresh Garlic (7pk)", "price": 1.99 }
  ],
  "total_price": 16.45,
  "cost_per_person": 8.23
}
```

#### Grounded Assistant Response:
> "Here is a 100% dairy-free **Thai Green Curry with Jasmine Rice** dinner plan for 2. The recipe naturally uses plant-based canned coconut milk instead of dairy cream. 
> 
> You can purchase the complete shopping list at **PAK'nSAVE Sylvia Park**:
> - **Chicken Breast (500g)**: \$6.99
> - **Canned Coconut Milk (400ml)**: \$1.49
> - **Jasmine Rice (1kg)**: \$2.69
> - **White Potatoes (1kg)**: \$3.29
> - **Fresh Garlic (7pk)**: \$1.99
>
> **Total Basket Price**: **\$16.45** (\$8.23 per person)."

---

## 4. Star Representative Demo Recipes (100% Ingredient Match)

These 2 curated star recipes have **100% full ingredient coverage** in our August 28, 2026 supermarket product dataset, demonstrating end-to-end multi-store price comparisons, itemized basket totals, and exact cost savings for demo presentations.

### 🌟 1. Beef Sunday Roast (British Cuisine / Traditional Family Meal)
- **Target Persona**: Families or flatmates hosting a weekend dinner.
- **Ingredient Coverage**: **8 / 8 Ingredients (100% Full Match)**
- **Matched Supermarket Items (Aug 28, 2026 Snapshot)**:
  - `Beef`: Slow Cooker Roast Beef Recipe Base / Mince / Beef Steak (\$1.89 – \$9.99)
  - `Potatoes`: White Washed Potatoes 1kg (\$2.99)
  - `Broccoli`: Fresh Broccoli 1 head (\$1.79 – \$2.49)
  - `Carrots`: Orange Carrots 1kg (\$1.99)
  - `Eggs`: Colony / Free Range Eggs 6pk (\$4.49 – \$8.99)
  - `Plain Flour`: Pure Plain Flour 1.5kg (\$1.89)
  - `Milk`: Standard / Soy Milk 1L (\$1.49 – \$2.99)
  - `Cooking Oil`: Canola Oil 500ml (\$1.89)
- **Basket Cost Comparison**:
  - **PAK'nSAVE Mt Albert Total**: **\$19.84** *(Cheapest Option)*
  - **New World Newmarket Total**: **\$22.85**
  - **Cost Difference**: Save **\$3.01 (13.2%)** by shopping at PAK'nSAVE.

---

### 🌟 2. Classic Breakfast Scramble & Toast (Western Cuisine / Everyday Staples)
- **Target Persona**: Everyday shoppers checking price fluctuations on high-frequency pantry staples.
- **Ingredient Coverage**: **5 / 5 Ingredients (100% Full Match)**
- **Matched Supermarket Items (Aug 28, 2026 Snapshot)**:
  - `Eggs`: Mixed Grade Free Range Eggs 12pk (\$8.99)
  - `Fresh Milk`: Standard Milk 2L (\$4.79)
  - `Pure Butter`: Tararua / Pams Pure Butter 500g (\$8.89)
  - `Toast Bread`: White Sliced Toast Bread 600g (\$2.89)
  - `Cooking Oil / Spread`: Canola Oil (\$1.89)
- **Basket Cost Comparison**:
  - **PAK'nSAVE Lincoln Road Total**: **\$21.06** *(Cheapest Option)*
  - **New World New Lynn Total**: **\$24.36**
  - **Cost Difference**: Save **\$3.30 (13.5%)** by shopping at PAK'nSAVE.
