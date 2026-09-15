export default function Composer({
  draft,
  isLoading,
  onDraftChange,
  onSubmit,
}) {
  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSubmit();
    }
  }

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <label className="sr-only" htmlFor="chat-message">
        Ask a grocery or meal planning question
      </label>

      <textarea
        id="chat-message"
        value={draft}
        onChange={(event) => onDraftChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask about prices or meal plans..."
        rows="1"
        disabled={isLoading}
      />

      <button
        type="submit"
        disabled={isLoading || !draft.trim()}
      >
        {isLoading ? "Working…" : "Send"}
      </button>
    </form>
  );
}
