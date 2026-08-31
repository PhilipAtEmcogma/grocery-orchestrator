import { useState } from "react";
import { sendChat } from "./api/chatClient";
import "./index.css";

function App() {
  const [message, setMessage] = useState("");
  const [response, setResponse] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();

    if (!message.trim() || loading) {
      return;
    }

    setLoading(true);
    setError("");

    try {
      const result = await sendChat({
        version: "1.0",
        session_id: "sess-browser01",
        turn_id: crypto.randomUUID(),
        message: message.trim(),
      });

      setResponse(result.body);
      setMessage("");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main>
      <h1>Smart Grocery Assistant</h1>

      <form onSubmit={handleSubmit}>
        <input
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="Ask about grocery prices..."
          maxLength={2000}
        />

        <button type="submit" disabled={loading}>
          {loading ? "Checking..." : "Send"}
        </button>
      </form>

      {error && <p>{error}</p>}

      {response && (
        <pre>{JSON.stringify(response, null, 2)}</pre>
      )}
    </main>
  );
}

export default App;