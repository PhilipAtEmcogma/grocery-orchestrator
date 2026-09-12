function formatMoney(value) {
  if (value === null || value === undefined || value === "") {
    return "Price unavailable";
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

export default function PriceComparisonCard({ comparison, citations }) {
  const options = comparison?.options ?? comparison?.items ?? [];

  if (options.length === 0) {
    return (
      <section className="result-card" aria-label="Price comparison">
        <div className="result-card__header">
          <div>
            <p className="result-card__eyebrow">Price check</p>
            <h2>Price comparison</h2>
          </div>
          <span className="result-card__icon" aria-hidden="true">
            🛒
          </span>
        </div>

        <p className="muted-text result-card__empty">
          The service returned no comparable products for this request.
        </p>
      </section>
    );
  }

  return (
    <section className="result-card" aria-label="Price comparison">
      <div className="result-card__header">
        <div>
          <p className="result-card__eyebrow">Price check</p>
          <h2>Price comparison</h2>
          <p className="muted-text">
            Retrieved supermarket prices and sources
          </p>
        </div>

        <span className="result-card__icon" aria-hidden="true">
          🛒
        </span>
      </div>

      {comparison?.reasoning && (
        <p className="comparison-reasoning">
          {comparison.reasoning}
        </p>
      )}

      <div className="price-list">
        {options.map((item, index) => {
          const citation = citations?.[item.citation_ref];

          if (!citation) {
            return (
              <div
                className="price-row"
                key={`${item.citation_ref ?? "missing"}-${index}`}
              >
                <div className="price-row__product">
                  <strong>Product source unavailable</strong>
                  <span>
                    Citation {item.citation_ref ?? "unknown"} could not be
                    resolved.
                  </span>
                </div>
              </div>
            );
          }

          return (
            <div
              className="price-row"
              key={citation.ref ?? item.citation_ref ?? index}
            >
              <div className="price-row__product">
                <strong>
                  {citation.product_name ??
                    citation.display_name ??
                    "Product"}
                </strong>

                <span>
                  {citation.store ?? "Store unavailable"}
                  {citation.store_location
                    ? ` · ${citation.store_location}`
                    : ""}
                </span>

                <span className="price-row__meta">
                  {citation.unit ?? "Unit unavailable"}
                  {citation.on_special ? " · On special" : ""}
                  {citation.valid_date
                    ? ` · ${citation.valid_date}`
                    : ""}
                </span>
              </div>

              <div className="price-row__price">
                <strong>{formatMoney(citation.price_nzd)}</strong>

                {item.is_cheapest && (
                  <span className="price-badge">Cheapest</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <footer className="result-card__footer">
        Prices reflect the retrieved supermarket source data.
      </footer>
    </section>
  );
}
