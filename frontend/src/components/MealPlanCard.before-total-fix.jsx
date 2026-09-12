function formatMoney(value) {
  const amount = Number(value);

  if (Number.isNaN(amount)) {
    return value ? `$${value}` : "—";
  }

  return new Intl.NumberFormat("en-NZ", {
    style: "currency",
    currency: "NZD",
  }).format(amount);
}

export default function MealPlanCard({ mealPlan, citations }) {
  if (!mealPlan) {
    return null;
  }

  const meals = mealPlan.meals ?? [];
  const shoppingList = mealPlan.shopping_list ?? [];

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
          <span>Total</span>
          <strong>{formatMoney(mealPlan.total_nzd)}</strong>
          {mealPlan.budget_nzd && (
            <small>Budget: {formatMoney(mealPlan.budget_nzd)}</small>
          )}
        </div>
      </div>

      {meals.length > 0 && (
  <div className="meal-section">
    <h3>Meals</h3>

    {meals.map((meal, index) => (
      <div className="meal-block" key={`${meal.name}-${index}`}>
        <div className="meal-block__header">
          <strong>{meal.name}</strong>
          <strong>{formatMoney(meal.total_nzd)}</strong>
        </div>

        {meal.ingredients?.length > 0 && (
          <div className="ingredient-list">
            {meal.ingredients.map((ingredient, ingredientIndex) => {
              const citation =
                citations?.[ingredient.citation_ref];

              const price =
                ingredient.price_nzd ??
                ingredient.unit_price_nzd ??
                citation?.price_nzd;

              return (
                <div
                  className="ingredient-row"
                  key={`${ingredient.item}-${ingredientIndex}`}
                >
                  <div className="ingredient-details">
                    <strong>{ingredient.item}</strong>
                    <span>{ingredient.qty ?? "Quantity not specified"}</span>

                    {citation && (
                      <span className="shopping-source">
                        {citation.store}
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

            return (
              <div className="shopping-row" key={`${item.item}-${index}`}>
                <div>
                  <strong>{item.item}</strong>
                  <span>{item.qty ?? "Quantity not specified"}</span>

                  {citation && (
                    <span className="shopping-source">
                      {citation.store}
                      {citation.store_location ? ` · ${citation.store_location}` : ""}
                    </span>
                  )}
                </div>

                <strong>{formatMoney(item.price_nzd ?? citation?.price_nzd)}</strong>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
