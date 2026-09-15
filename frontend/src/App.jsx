import { useState, useEffect } from "react";
import "./App.css";

import BudgetControls from "./components/BudgetControls";
import ChatHeader from "./components/ChatHeader";
import Composer from "./components/Composer";
import MessageList from "./components/MessageList";
import { sendChat as sendChatMessage } from "./api/chatClient";

const WELCOME_MESSAGE = {
  id: "welcome",
  role: "assistant",
  status: "complete",
  text: "Hello! I can compare grocery prices, create budget meal plans, and prepare a shopping list with sourced prices.",
  citations: {},
};

function newId(prefix) {
  if (globalThis.crypto?.randomUUID) {
    return `${prefix}-${globalThis.crypto.randomUUID()}`;
  }

  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

// Persisted per browser tab (sessionStorage) so a page refresh doesn't lose
// the conversation. Swap sessionStorage for localStorage on both functions
// below if you want the chat to survive closing the tab too.
const SESSION_STORAGE_KEY = "grocery-session-id";
const HISTORY_STORAGE_KEY = "grocery-chat-history";

function loadStoredSessionId() {
  try {
    const existing = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (existing) {
      return existing;
    }
    const created = newId("sess");
    sessionStorage.setItem(SESSION_STORAGE_KEY, created);
    return created;
  } catch {
    // Storage unavailable (private browsing, etc.) — the chat still works,
    // it just won't survive a refresh.
    return newId("sess");
  }
}

function storeSessionId(id) {
  try {
    sessionStorage.setItem(SESSION_STORAGE_KEY, id);
  } catch {
    // Ignore — see loadStoredSessionId.
  }
}

function loadStoredMessages() {
  try {
    const raw = sessionStorage.getItem(HISTORY_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) {
        return parsed;
      }
    }
  } catch {
    // Malformed or unavailable storage — fall back to a fresh welcome.
  }
  return [WELCOME_MESSAGE];
}

function extractAssistantContent(events) {
  const citations = {};
  const textParts = [];
  const comparisons = [];
  let mealPlan = null;
  let notice = null;
  let noData = null;
  let error = null;

  for (const event of events ?? []) {
    if (event.type === "citation") {
      const citation = event.citation ?? event.data;

      if (citation?.ref) {
        citations[citation.ref] = citation;
      }
    }

    if (event.type === "token" && event.text) {
      textParts.push(event.text);
    }

    if (event.type === "price_comparison") {
  const data = event.data ?? event.comparison ?? event;

  comparisons.push({
    ...data,
    options: data?.options ?? data?.items ?? [],
  });
}

    if (event.type === "meal_plan") {
      mealPlan = event.data ?? event.meal_plan ?? event;
    }

    if (event.type === "notice") {
      notice = event.data ?? event;
    }

    if (event.type === "no_data") {
      noData = event.data ?? event;
    }

    if (event.type === "error") {
      error = event.data ?? event;
    }
  }

  return {
    status: "complete",
    text: textParts.join(""),
    citations,
    comparisons,
    mealPlan,
    notice,
    noData,
    error,
  };
}

export default function App() {
  const [sessionId, setSessionId] = useState(loadStoredSessionId);
  const [messages, setMessages] = useState(loadStoredMessages);
  const [draft, setDraft] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const [location, setLocation] = useState("");
  const [budget, setBudget] = useState("30");
  const [people, setPeople] = useState("3");
  const [days, setDays] = useState("3");

  // Keep the visible history in sync with storage on every change, so a
  // refresh mid-conversation restores exactly what was on screen.
  useEffect(() => {
    try {
      sessionStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(messages));
    } catch {
      // Storage full or unavailable — chat still works, it just won't persist.
    }
  }, [messages]);

  function clearConversation() {
    setMessages([
      {
        ...WELCOME_MESSAGE,
        id: newId("welcome"),
      },
    ]);

    // A cleared chat is a new conversation, so it gets a new session_id too
    // — otherwise the backend would see one session_id spanning two
    // unrelated conversations.
    const freshSessionId = newId("sess");
    storeSessionId(freshSessionId);
    setSessionId(freshSessionId);
  }

  async function handleSubmit() {
    const message = draft.trim();

    if (!message || isLoading) {
      return;
    }

    const turnId = newId("turn");

    const userMessage = {
      id: newId("user"),
      role: "user",
      status: "complete",
      text: message,
    };

    const assistantMessageId = newId("assistant");

    const loadingAssistantMessage = {
      id: assistantMessageId,
      role: "assistant",
      status: "loading",
      text: "",
      citations: {},
    };

    setMessages((current) => [
      ...current,
      userMessage,
      loadingAssistantMessage,
    ]);

    setDraft("");
    setIsLoading(true);

    try {
      const response = await sendChatMessage({
        version: "1.0",
        session_id: sessionId,
        turn_id: turnId,
        message,
        hints: {
          household_size: Number(people),
          budget_nzd: Number(budget),
          days: Number(days),
        },
        ...(location
          ? {
              location: {
                region: location,
              },
            }
          : {}),
      });

      const body = response?.body;

      if (!body?.events) {
        throw new Error(
          `The service returned an unexpected response (HTTP ${response?.status ?? "unknown"}).`,
        );
      }

      const content = extractAssistantContent(body.events);

      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId
            ? { ...item, ...content }
            : item,
        ),
      );
    } catch (requestError) {
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId
            ? {
                ...item,
                status: "complete",
                error: {
                  message:
                    requestError?.message ??
                    "Sorry, the grocery service could not be reached. Please try again.",
                },
              }
            : item,
        ),
      );
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="app-shell">
      <section className="chat-window" aria-label="Smart Grocery Assistant">
        <ChatHeader onClear={clearConversation} />

        <BudgetControls
          location={location}
          budget={budget}
          people={people}
          days={days}
          onLocationChange={setLocation}
          onBudgetChange={setBudget}
          onPeopleChange={setPeople}
          onDaysChange={setDays}
        />

        <MessageList messages={messages} />

        <Composer
          draft={draft}
          isLoading={isLoading}
          onDraftChange={setDraft}
          onSubmit={handleSubmit}
        />
      </section>
    </div>
  );
}
