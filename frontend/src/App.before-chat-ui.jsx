import { useMemo, useState } from "react";
import {
  AlertCircle,
  Bot,
  CheckSquare,
  Clock3,
  Info,
  MapPin,
  PackageSearch,
  Send,
  ShoppingBag,
  Sparkles,
  Square,
  Store,
  Tag,
  User,
  Utensils,
} from "lucide-react";
import { sendChat } from "./api/chatClient";
import "./App.css";

const INITIAL_MESSAGE = {
  id: "welcome",
  sender: "ai",
  events: [
    {
      seq: 0,
      type: "token",
      text: "Hello! I can compare grocery prices, create budget meal plans, and prepare a shopping list with sourced prices.",
    },
    {
      seq: 1,
      type: "done",
    },
  ],
};

function createId(prefix) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function getSessionId() {
  const storageKey = "grocery-session-id";
  let sessionId = localStorage.getItem(storageKey);

  if (!sessionId) {
    sessionId = createId("sess");
    localStorage.setItem(storageKey, sessionId);
  }

  return sessionId;
}

function getEventMessage(event) {
  return (
    event.message ||
    event.text ||
    event.content ||
    event.notice?.message ||
    event.error?.message ||
    event.no_data?.message ||
    ""
  );
}

function formatMoney(amount) {
  if (amount === null || amount === undefined || amount === "") {
    return "Price unavailable";
  }

  return new Intl.NumberFormat("en-NZ", {
    style: "currency",
    currency: "NZD",
  }).format(Number(amount));
}

function normaliseResponseEvents(responseBody) {
  const events = Array.isArray(responseBody?.events)
    ? responseBody.events
    : [];

  return [...events].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
}

function buildCitationMap(events) {
  return events.reduce((map, event) => {
    if (event.type === "citation" && event.citation?.ref) {
      map[event.citation.ref] = event.citation;
    }

    return map;
  }, {});
}

function findCitationRef(value) {
  if (!value || typeof value !== "object") {
    return null;
  }

  return (
    value.citation_ref ||
    value.citationRef ||
    value.price_citation_ref ||
    value.product_citation_ref ||
    null
  );
}

function CitationDetails({ citationRef, citations }) {
  const citation = citations[citationRef];

  if (!citationRef) {
    return null;
  }

  if (!citation) {
    return (
      <p className="citation-warning">
        Price source unavailable for reference: {citationRef}
      </p>
    );
  }

  return (
    <div className="citation-details">
      <div className="citation-topline">
        <span className="citation-price">
          {formatMoney(citation.price_nzd)}
        </span>

        {citation.on_special && (
          <span className="special-badge">
            <Tag size={13} />
            Special
          </span>
        )}
      </div>

      <p>
        <Store size={14} />
        {citation.store_location
          ? `${citation.store} — ${citation.store_location}`
          : citation.store}
      </p>

      <p>
        <Clock3 size={14} />
        Valid date: {citation.valid_date || "Not provided"}
      </p>

      {citation.unit && <p>Pack size: {citation.unit}</p>}

      {citation.unit_price_nzd && (
        <p>Unit price: {formatMoney(citation.unit_price_nzd)}</p>
      )}
    </div>
  );
}

function PriceComparisonCard({ event, citations }) {
  const comparison =
    event.price_comparison || event.comparison || event.payload || event;

  const items =
    comparison.items ||
    comparison.products ||
    comparison.results ||
    comparison.comparisons ||
    [];

  const title =
    comparison.title ||
    comparison.product_name ||
    "Price comparison";

  return (
    <section className="result-card price-card">
      <div className="card-heading">
        <PackageSearch size={19} />
        <div>
          <h2>{title}</h2>
          <p>Retrieved supermarket prices and sources</p>
        </div>
      </div>

      {Array.isArray(items) && items.length > 0 ? (
        <div className="price-grid">
          {items.map((item, index) => {
            const citationRef = findCitationRef(item);
            const citation = citations[citationRef];
            const productName =
              item.product_name ||
              item.name ||
              item.item ||
              citation?.product_name ||
              `Product ${index + 1}`;

            return (
              <article className="price-item" key={`${productName}-${index}`}>
                <h3>{productName}</h3>

                {item.is_cheapest || item.cheapest ? (
                  <span className="cheapest-badge">Best price found</span>
                ) : null}

                <CitationDetails
                  citationRef={citationRef}
                  citations={citations}
                />
              </article>
            );
          })}
        </div>
      ) : (
        <p className="empty-state">
          The service returned no comparable products for this request.
        </p>
      )}
    </section>
  );
}

function MealPlanCard({ event, citations, onToggleItem, checkedItems }) {
  const plan = event.meal_plan || event.plan || event.payload || event;
  const meals = plan.meals || [];
  const shoppingList =
    plan.shopping_list || plan.shoppingList || plan.ingredients || [];

  return (
    <section className="result-card meal-plan-card">
      <div className="card-heading">
        <Utensils size={19} />
        <div>
          <h2>{plan.title || "Budget meal plan"}</h2>
          <p>
            {plan.household_size
              ? `For ${plan.household_size} people`
              : "Personalised to your request"}
            {plan.days ? ` • ${plan.days} days` : ""}
          </p>
        </div>
      </div>

      <div className="meal-plan-summary">
        {plan.budget_nzd && (
          <span>Budget: {formatMoney(plan.budget_nzd)}</span>
        )}

        {plan.total_nzd && (
          <span>Total: {formatMoney(plan.total_nzd)}</span>
        )}

        {plan.grand_total && (
          <span>Total: {formatMoney(plan.grand_total)}</span>
        )}

        {plan.remaining_budget_nzd && (
          <span>
            Remaining: {formatMoney(plan.remaining_budget_nzd)}
          </span>
        )}

        {plan.repair_attempts !== undefined && (
          <span>Plan checks: {plan.repair_attempts}</span>
        )}
      </div>

      {meals.length > 0 && (
        <div className="meal-grid">
          {meals.map((meal, mealIndex) => (
            <article className="meal-card" key={`${meal.name}-${mealIndex}`}>
              <h3>{meal.name || `Meal ${mealIndex + 1}`}</h3>

              {meal.description && <p>{meal.description}</p>}

              <ul>
                {(meal.ingredients || []).map((ingredient, index) => {
                  const citationRef = findCitationRef(ingredient);
                  const citation = citations[citationRef];

                  return (
                    <li key={`${ingredient.item || ingredient.name}-${index}`}>
                      <div>
                        <strong>
                          {ingredient.item ||
                            ingredient.name ||
                            citation?.product_name ||
                            "Ingredient"}
                        </strong>
                        {ingredient.quantity || ingredient.qty ? (
                          <span>
                            {ingredient.quantity || ingredient.qty}
                          </span>
                        ) : null}
                      </div>

                      <CitationDetails
                        citationRef={citationRef}
                        citations={citations}
                      />
                    </li>
                  );
                })}
              </ul>

              {meal.total_nzd && (
                <p className="meal-total">
                  Meal total: {formatMoney(meal.total_nzd)}
                </p>
              )}

              {meal.total && (
                <p className="meal-total">
                  Meal total: {formatMoney(meal.total)}
                </p>
              )}
            </article>
          ))}
        </div>
      )}

      {shoppingList.length > 0 && (
        <div className="shopping-section">
          <div className="shopping-heading">
            <ShoppingBag size={19} />
            <h3>Shopping list</h3>
          </div>

          <ul className="shopping-list">
            {shoppingList.map((item, index) => {
              const itemId =
                item.id ||
                `${item.item || item.name || "item"}-${index}`;

              const itemName =
                item.item ||
                item.name ||
                item.product_name ||
                `Item ${index + 1}`;

              const citationRef = findCitationRef(item);
              const isChecked = Boolean(checkedItems[itemId]);

              return (
                <li key={itemId}>
                  <button
                    type="button"
                    onClick={() => onToggleItem(itemId)}
                    aria-label={`Mark ${itemName} as ${
                      isChecked ? "not purchased" : "purchased"
                    }`}
                  >
                    {isChecked ? (
                      <CheckSquare size={20} />
                    ) : (
                      <Square size={20} />
                    )}
                  </button>

                  <div className="shopping-item-content">
                    <span className={isChecked ? "checked" : ""}>
                      {itemName}
                    </span>

                    {item.quantity || item.qty ? (
                      <small>{item.quantity || item.qty}</small>
                    ) : null}

                    <CitationDetails
                      citationRef={citationRef}
                      citations={citations}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </section>
  );
}

function AssistantResponse({ events, onToggleItem, checkedItems }) {
  const sortedEvents = useMemo(
    () => [...events].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0)),
    [events]
  );

  const citations = useMemo(
    () => buildCitationMap(sortedEvents),
    [sortedEvents]
  );

  return (
    <div className="assistant-response">
      {sortedEvents.map((event, index) => {
        if (event.type === "session" || event.type === "done") {
          return null;
        }

        if (event.type === "citation") {
          return null;
        }

        if (event.type === "intent") {
          const intent =
            event.intent?.name ||
            event.intent?.type ||
            event.intent ||
            "request";

          return (
            <p className="intent-label" key={`intent-${index}`}>
              <Sparkles size={14} />
              Preparing {String(intent).replaceAll("_", " ")}
            </p>
          );
        }

        if (event.type === "token") {
          const text = getEventMessage(event);

          return text ? (
            <p className="assistant-text" key={`token-${index}`}>
              {text}
            </p>
          ) : null;
        }

        if (event.type === "price_comparison") {
          return (
            <PriceComparisonCard
              key={`price-${index}`}
              event={event}
              citations={citations}
            />
          );
        }

        if (event.type === "meal_plan") {
          return (
            <MealPlanCard
              key={`meal-${index}`}
              event={event}
              citations={citations}
              onToggleItem={onToggleItem}
              checkedItems={checkedItems}
            />
          );
        }

        if (event.type === "notice") {
          return (
            <div className="notice-box" key={`notice-${index}`}>
              <Info size={17} />
              <span>{getEventMessage(event)}</span>
            </div>
          );
        }

        if (event.type === "no_data") {
          return (
            <div className="no-data-box" key={`no-data-${index}`}>
              <PackageSearch size={18} />
              <span>
                {getEventMessage(event) ||
                  "I do not have price data for that item yet."}
              </span>
            </div>
          );
        }

        if (event.type === "error") {
          return (
            <div className="error-box" key={`error-${index}`}>
              <AlertCircle size={18} />
              <span>
                {getEventMessage(event) ||
                  "The request could not be completed."}
              </span>
            </div>
          );
        }

        return null;
      })}
    </div>
  );
}

function App() {
  const [messages, setMessages] = useState([INITIAL_MESSAGE]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [locationLabel, setLocationLabel] = useState("");
  const [budget, setBudget] = useState("");
  const [householdSize, setHouseholdSize] = useState("");
  const [checkedItems, setCheckedItems] = useState({});

  function toggleItem(itemId) {
    setCheckedItems((current) => ({
      ...current,
      [itemId]: !current[itemId],
    }));
  }

  async function handleSubmit(event) {
    event.preventDefault();

    const message = input.trim();

    if (!message || loading) {
      return;
    }

    const turnId = createId("turn");

    const request = {
      version: "1.0",
      session_id: getSessionId(),
      turn_id: turnId,
      message,
    };

    const hints = {};

    if (budget) {
      hints.budget_nzd = Number(budget);
    }

    if (householdSize) {
      hints.household_size = Number(householdSize);
    }

    if (Object.keys(hints).length > 0) {
      request.hints = hints;
    }

    if (locationLabel.trim()) {
      request.location = {
        label: locationLabel.trim(),
      };
    }

    setMessages((current) => [
      ...current,
      {
        id: turnId,
        sender: "user",
        text: message,
      },
    ]);

    setInput("");
    setLoading(true);

    try {
      const result = await sendChat(request);
      const events = normaliseResponseEvents(result.body);

      setMessages((current) => [
        ...current,
        {
          id: `response-${turnId}`,
          sender: "ai",
          events:
            events.length > 0
              ? events
              : [
                  {
                    seq: 0,
                    type: "error",
                    message: "The service returned an empty response.",
                  },
                  {
                    seq: 1,
                    type: "done",
                  },
                ],
        },
      ]);
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: `error-${turnId}`,
          sender: "ai",
          events: [
            {
              seq: 0,
              type: "error",
              message:
                error.message ||
                "I could not reach the grocery assistant. Please try again.",
            },
            {
              seq: 1,
              type: "done",
            },
          ],
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="assistant-shell">
      <header className="app-header">
        <div className="brand">
          <div className="brand-icon">
            <Utensils size={23} />
          </div>

          <div>
            <h1>Smart Grocery Assistant</h1>
            <p>Compare prices, plan meals, and shop within your budget.</p>
          </div>
        </div>
      </header>

      <section className="chat-container">
        <div className="quick-controls">
          <label>
            <MapPin size={16} />
            <input
              value={locationLabel}
              onChange={(event) => setLocationLabel(event.target.value)}
              placeholder="Optional suburb or store area"
            />
          </label>

          <label>
            Budget (NZD)
            <input
              type="number"
              min="1"
              step="1"
              value={budget}
              onChange={(event) => setBudget(event.target.value)}
              placeholder="30"
            />
          </label>

          <label>
            People
            <input
              type="number"
              min="1"
              max="20"
              value={householdSize}
              onChange={(event) => setHouseholdSize(event.target.value)}
              placeholder="3"
            />
          </label>
        </div>

        <div className="message-list">
          {messages.map((message) => (
            <article
              className={`message-row ${message.sender}`}
              key={message.id}
            >
              <div className="avatar">
                {message.sender === "ai" ? (
                  <Bot size={18} />
                ) : (
                  <User size={18} />
                )}
              </div>

              <div className="message-content">
                {message.sender === "user" ? (
                  <p className="message-text">{message.text}</p>
                ) : (
                  <AssistantResponse
                    events={message.events}
                    onToggleItem={toggleItem}
                    checkedItems={checkedItems}
                  />
                )}
              </div>
            </article>
          ))}

          {loading && (
            <div className="loading-indicator">
              <Bot size={18} />
              <span>Checking prices and preparing your response...</span>
            </div>
          )}
        </div>

        <form className="chat-form" onSubmit={handleSubmit}>
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Ask about grocery prices or a meal plan..."
            maxLength={2000}
            aria-label="Chat message"
            disabled={loading}
          />

          <button type="submit" disabled={loading || !input.trim()}>
            <Send size={18} />
            Send
          </button>
        </form>
      </section>
    </main>
  );
}

export default App;