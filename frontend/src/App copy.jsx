import React, { useState } from "react";
import {
  Send,
  Bot,
  User,
  CheckSquare,
  Square,
  ShoppingBag,
  Utensils,
} from "lucide-react";

export default function GroceryAssistant() {
  const [messages, setMessages] = useState([
    {
      id: 1,
      sender: "ai",
      text: "Hello! I'm your AI Grocery & Recipe Assistant. What are we cooking today, or what ingredients do you have on hand?",
      type: "text",
    },
  ]);

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSend = (event) => {
    event.preventDefault();

    if (!input.trim()) {
      return;
    }

    const userMessage = {
      id: Date.now(),
      sender: "user",
      text: input,
      type: "text",
    };

    setMessages((previous) => [...previous, userMessage]);
    setInput("");
    setLoading(true);

    setTimeout(() => {
      const aiResponse = {
        id: Date.now() + 1,
        sender: "ai",
        text: "Here is a quick recipe and your consolidated grocery list based on your request!",
        type: "rich-content",
        recipe: {
          title: "15-Minute Garlic Butter Salmon",
          prepTime: "15 mins",
          servings: "2",
          ingredients: [
            { name: "Salmon fillets", qty: "2" },
            { name: "Garlic", qty: "3 cloves" },
            { name: "Butter", qty: "2 tbsp" },
            { name: "Fresh Lemon", qty: "1" },
          ],
        },
        groceryList: [
          { id: "g1", item: "Salmon fillets (2)", checked: false },
          { id: "g2", item: "Garlic bulb", checked: false },
          { id: "g3", item: "Unsalted Butter", checked: false },
          { id: "g4", item: "Fresh Lemons (2)", checked: false },
        ],
      };

      setMessages((previous) => [...previous, aiResponse]);
      setLoading(false);
    }, 1200);
  };

  const toggleGroceryItem = (messageId, itemId) => {
    setMessages((previous) =>
      previous.map((message) => {
        if (message.id !== messageId || !message.groceryList) {
          return message;
        }

        return {
          ...message,
          groceryList: message.groceryList.map((item) =>
            item.id === itemId
              ? { ...item, checked: !item.checked }
              : item
          ),
        };
      })
    );
  };

  return (
    <main className="assistant-shell">
      <header className="app-header">
        <div className="brand">
          <div className="brand-icon">
            <Utensils size={22} />
          </div>
          <div>
            <h1>Smart Grocery Assistant</h1>
            <p>Plan meals. Compare prices. Shop smarter.</p>
          </div>
        </div>
      </header>

      <section className="chat-container">
        <div className="message-list">
          {messages.map((message) => (
            <article
              key={message.id}
              className={`message-row ${message.sender}`}
            >
              <div className="avatar">
                {message.sender === "ai" ? (
                  <Bot size={18} />
                ) : (
                  <User size={18} />
                )}
              </div>

              <div className="message-content">
                <p className="message-text">{message.text}</p>

                {message.type === "rich-content" && (
                  <div className="rich-content">
                    <div className="recipe-card">
                      <div className="card-heading">
                        <Utensils size={18} />
                        <h2>{message.recipe.title}</h2>
                      </div>

                      <div className="recipe-meta">
                        <span>Prep: {message.recipe.prepTime}</span>
                        <span>Servings: {message.recipe.servings}</span>
                      </div>

                      <h3>Ingredients</h3>

                      <ul>
                        {message.recipe.ingredients.map((ingredient) => (
                          <li key={ingredient.name}>
                            <span>{ingredient.name}</span>
                            <span>{ingredient.qty}</span>
                          </li>
                        ))}
                      </ul>
                    </div>

                    <div className="grocery-card">
                      <div className="card-heading">
                        <ShoppingBag size={18} />
                        <h2>Shopping List</h2>
                      </div>

                      <ul className="shopping-list">
                        {message.groceryList.map((item) => (
                          <li key={item.id}>
                            <button
                              type="button"
                              onClick={() =>
                                toggleGroceryItem(message.id, item.id)
                              }
                              aria-label={`Mark ${item.item} as ${
                                item.checked ? "not purchased" : "purchased"
                              }`}
                            >
                              {item.checked ? (
                                <CheckSquare size={18} />
                              ) : (
                                <Square size={18} />
                              )}
                            </button>

                            <span className={item.checked ? "checked" : ""}>
                              {item.item}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>
                )}
              </div>
            </article>
          ))}

          {loading && (
            <div className="loading-indicator">
              <Bot size={18} />
              <span>Preparing your grocery plan...</span>
            </div>
          )}
        </div>

        <form className="chat-form" onSubmit={handleSend}>
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Ask for a meal plan or grocery comparison..."
            aria-label="Chat message"
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