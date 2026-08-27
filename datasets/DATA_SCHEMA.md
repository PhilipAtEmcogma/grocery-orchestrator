# Smart Grocery Data Schema

## 1. Data Source

### Product Data

Product prices come from Foodstuffs online shopping data for two fixed stores.

PAK'nSAVE Lincoln Road:

- Store ID: `92086ded-a55d-4241-a364-7d7ea91531b4`
- Source: PAK'nSAVE online shopping website frontend network responses, collected with Lincoln Road selected in a browser login context.
- Processed count: `285` products after removing duplicate product IDs within the same store.

New World Albany:

- Store ID: `c8998066-d39b-401c-aa6b-d6d18f8d122f`
- Source: team-provided New World Albany sample CSV, processed into the dataset files listed below.
- Processed count: `300` products.

Processed product files:

- `datasets/data/processed/pakn_save_lincon_road_products_latest.csv`
- `datasets/data/processed/pakn_save_lincon_road_products_latest.json`
- `datasets/data/processed/new_world_albany_products_latest.csv`
- `datasets/data/processed/new_world_albany_products_latest.json`

Products table: `SmartGroceryProducts`

| Field | Type | PK | Meaning |
| --- | --- | --- | --- |
| `primary_key` | String | PK | Unique DynamoDB primary key, generated as `pakn_save_<product_id>` or `new_world_<product_id>`. |
| `store_id` | String |  | Fixed store identifier. |
| `store_name` | String |  | Store display name. |
| `product_id` | String |  | Original retailer product identifier. |
| `product_name` | String |  | Product display name. |
| `brand` | String or NULL |  | Product brand; may be empty for loose produce or in-store prepared items. |
| `size` | String |  | Pack size or selling unit. |
| `price` | Number | GSI sort key | Current product price in NZD. |
| `category` | String | GSI partition key | Cleaned product category used for demo filtering. |

### Recipe Data

Recipe data comes from TheMealDB official API:

- API docs: https://www.themealdb.com/api.php
- Terms: https://www.themealdb.com/terms_of_use.php
- Selection strategy: Asian regions plus British/United Kingdom recipes.
- Current processed count: `175` recipes.

Processed recipe files:

- `datasets/data/processed/recipes_latest.csv`
- `datasets/data/processed/recipes_latest.json`

Recipes table: `SmartGroceryRecipes`

| Field | Type | PK | Meaning |
| --- | --- | --- | --- |
| `recipe_id` | String | PK | Stable recipe primary key, for example `mealdb#52893`. |
| `recipe_name` | String | GSI sort key | Cleaned recipe display name. |
| `category` | String | GSI partition key | Recipe category, such as `Chicken`, `Beef`, `Dessert`, or `Vegetarian`. |
| `area` | String | GSI partition key | Cuisine or regional area from TheMealDB. |
| `country` | String |  | Normalized country name when available. |
| `ingredients` | List of maps |  | Ingredient list with `name`, `measure`, and normalized `key`. |
| `ingredient_keys` | List of strings |  | Normalized ingredient names for simple matching. |
| `ingredient_count` | Number |  | Number of ingredients in the recipe. |
| `instructions` | String |  | Cooking instructions. |
| `thumbnail_url` | String |  | Recipe image URL. |
| `source_url` | String |  | Original recipe source URL when available. |
| `youtube_url` | String |  | YouTube tutorial URL when available. |
| `mealdb_url` | String |  | TheMealDB recipe page URL. |
| `source_terms_url` | String |  | TheMealDB terms URL. |
| `attribution` | String |  | Short attribution text for TheMealDB. |
| `fetched_at` | String |  | Timestamp when the recipe data was fetched. |
| `dataset_version` | String |  | Dataset version added to DynamoDB batch items during batch generation. |

## 2. DynamoDB Commands

Run commands from the repository root:

```bash
cd <repo-folder>
```

Create the product and recipe tables:

```bash
aws dynamodb create-table \
  --cli-input-json file://datasets/dynamodb_schema/products-table.json

aws dynamodb create-table \
  --cli-input-json file://datasets/dynamodb_schema/recipes-table.json
```

Import PAK'nSAVE product batches:

```bash
for f in datasets/data/dynamodb_products_batches/pakn_save/products_batch_*.json; do
  aws dynamodb batch-write-item --request-items file://$f
done
```

Import New World product batches:

```bash
for f in datasets/data/dynamodb_products_batches/new_world/products_batch_*.json; do
  aws dynamodb batch-write-item --request-items file://$f
done
```

Import recipe batches:

```bash
for f in datasets/data/dynamodb_recipe_batches/recipes_batch_*.json; do
  aws dynamodb batch-write-item --request-items file://$f
done
```

Current batch files:

- PAK'nSAVE products: `datasets/data/dynamodb_products_batches/pakn_save/products_batch_001.json` to `products_batch_012.json`
- New World products: `datasets/data/dynamodb_products_batches/new_world/products_batch_001.json` to `products_batch_012.json`
- Recipes: `datasets/data/dynamodb_recipe_batches/recipes_batch_001.json` to `recipes_batch_007.json`
