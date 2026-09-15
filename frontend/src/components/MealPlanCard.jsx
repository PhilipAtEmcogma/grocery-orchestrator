function formatMoney(value) {
  if (value === null || value === undefined || value === "") {
    return "—";
  }

  const amount = Number(value);

  if (Number.isNaN(amount)) {
    return `$${value}`;
  }

  return new Intl.NumberFormat("en-NZ", {
    style: "currency",
    currency: "NZD",
  }).format(amount);
}

function displayTotal(mealPlan) {
  return mealPlan.payable_total_nzd ?? mealPlan.total_nzd;
}

export default function MealPlanCard({ mealPlan, citations = {} }) {
  if (!mealPlan) {
    return null;
  }

  const meals = mealPlan.meals ?? [];
  const shoppingList = mealPlan.shopping_list ?? [];
  const baskets = mealPlan.baskets ?? [];
  const shoppingTotal = displayTotal(mealPlan);

  return (
    <section className="result-card meal-plan-card" aria-label="Budget meal plan">
      <div className="result-card__header">
        <div>
          <p className="result-card__eyebrow">Budget meal plan</p>
          <h2>{mealPlan.title ?? "Your meal plan"}</h2>
          <p className="muted-text">
            {mealPlan.household_size ?? "—"} people · {mealPlan.days ?? "—"} days
          </p>
        </div>

        <div className="meal-total">
          <span>Shopping total</span>
          <strong>{formatMoney(shoppingTotal)}</strong>

          {mealPlan.total_nzd !== undefined &&
            mealPlan.payable_total_nzd !== undefined &&
            mealPlan.total_nzd !== mealPlan.payable_total_nzd && (
              <small>
                Consumed cost: {formatMoney(mealPlan.total_nzd)}
              </small>
            )}

          {mealPlan.budget_nzd !== undefined && (
            <small>Budget: {formatMoney(mealPlan.budget_nzd)}</small>
          )}
        </div>
      </div>

      {meals.length > 0 && (
        <div className="meal-section">
          <h3>Meals</h3>

          {meals.map((meal, mealIndex) => (
            <div className="meal-block" key={`${meal.name ?? "meal"}-${mealIndex}`}>
              <div className="meal-block__header">
                <strong>{meal.name ?? "Meal"}</strong>
                <strong>
                  {meal.subtotal_nzd !== undefined
                    ? formatMoney(meal.subtotal_nzd)
                    : meal.total_nzd !== undefined
                      ? formatMoney(meal.total_nzd)
                      : "—"}
                </strong>
              </div>

              {meal.ingredients?.length > 0 && (
                <div className="ingredient-list">
                  {meal.ingredients.map((ingredient, ingredientIndex) => {
                    const citation = citations[ingredient.citation_ref];
                    const price =
                      ingredient.line_cost_nzd ??
                      ingredient.price_nzd ??
                      ingredient.unit_price_nzd ??
                      citation?.price_nzd;

                    return (
                      <div
                        className="ingredient-row"
                        key={`${ingredient.item ?? "ingredient"}-${ingredientIndex}`}
                      >
                        <div className="ingredient-details">
                          <strong>{ingredient.item ?? "Ingredient"}</strong>
                          <span>
                            {ingredient.qty ?? "Quantity not specified"}
                          </span>

                          {citation && (
                            <span className="shopping-source">
                              {citation.store ?? "Store unavailable"}
                              {citation.store_location
                                ? ` · ${citation.store_location}`
                                : ""}
                            </span>
                          )}
                        </div>

                        <strong className="ingredient-price">
                          {price !== undefined && price !== null
                            ? formatMoney(price)
                            : "Price unavailable"}
                        </strong>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {shoppingList.length > 0 && (
        <div className="meal-section">
          <h3>Shopping list</h3>

          {shoppingList.map((item, index) => {
            const citation = citations[item.citation_ref];
            const price =
              item.price_nzd ??
              item.purchase_price_nzd ??
              item.unit_price_nzd ??
              citation?.price_nzd;

            return (
              <div
                className="shopping-row"
                key={`${item.item ?? "item"}-${index}`}
              >
                <div>
                  <strong>{item.item ?? "Shopping item"}</strong>
                  <span>{item.qty ?? "Quantity not specified"}</span>

                  {citation && (
                    <span className="shopping-source">
                      {citation.store ?? "Store unavailable"}
                      {citation.store_location
                        ? ` · ${citation.store_location}`
                        : ""}
                    </span>
                  )}
                </div>

                <strong>
                  {price !== undefined && price !== null
                    ? formatMoney(price)
                    : "Price unavailable"}
                </strong>
              </div>
            );
          })}
        </div>
      )}

      {baskets.length > 0 && (
        <div className="meal-section">
          <h3>Store totals</h3>

          {baskets.map((basket, index) => (
            <div
              className="shopping-row"
              key={`${basket.store ?? "store"}-${basket.store_location ?? index}`}
            >
              <div>
                <strong>{basket.store ?? "Store"}</strong>
                <span>{basket.store_location ?? "Location unavailable"}</span>
              </div>

              <strong>{formatMoney(basket.basket_total_nzd)}</strong>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
