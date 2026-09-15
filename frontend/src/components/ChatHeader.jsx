export default function ChatHeader({ onClear }) {
  return (
    <header className="chat-header">
      <div>
        <p className="chat-header__eyebrow">Price comparison and meal planning</p>
        <h1 className="chat-header__title">Smart Grocery Assistant</h1>
        <p className="chat-header__subtitle">
          Compare prices, plan meals, and shop within your budget.
        </p>
      </div>

      <button
        type="button"
        className="clear-button"
        onClick={onClear}
        aria-label="Clear conversation"
      >
        Clear chat
      </button>
    </header>
  );
}
