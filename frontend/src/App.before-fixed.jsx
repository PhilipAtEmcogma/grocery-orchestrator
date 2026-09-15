import { useState } from "react";
import "./App.css";

import BudgetControls from "./components/BudgetControls";
import ChatHeader from "./components/ChatHeader";
import Composer from "./components/Composer";
import MessageList from "./components/MessageList";

// IMPORTANT:
// Keep the API import/function name used by your existing project.
// If your current file uses a different function name, replace this line
// and update the call inside handleSubmit accordingly.
import { sendChat as sendChatMessage } from "./api/chatClient";

const WELCOME_MESSAGE = {
  id: "welcome",
  role: "assistant",
  status: "complete",
  text: "Hello! I can compare grocery prices, create budget meal plans, and prepare a shopping list with sourced prices.",
  citations: {},
};

function newId(prefix) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function extractAssistantContent(events) {
  const citations = {};
  const textParts = [];
  let comparison = null;
  let mealPlan = null;
  let notice = null;
  let noData = null;
  let error = null;

  for (const event of events ?? []) {
    if (event.type === "citation" && event.citation?.ref) {
      citations[event.citation.ref] = event.citation;
    }

    if (event.type === "token" && event.text) {
      textParts.push(event.text);
    }

if (event.type === "price_comparison") {
  comparison = {
    ...event.data,
    options: event.data?.options ?? [],
  };
}
    if (event.type === "price_comparison") {
      comparison = event;
    }

    if (event.type === "meal_plan") {
if (event.type === "meal_plan") {
  mealPlan = event.data ?? event.meal_plan ?? event;
}
      mealPlan = event;
    }

    if (event.type === "notice") {
      notice = event;
    }

    if (event.type === "no_data") {
      noData = event;
    }

    if (event.type === "error") {
      error = event;
    }
  }

  return {
    status: "complete",
    text: textParts.join(""),
    citations,
    comparison,
    mealPlan,
    notice,
    noData,
    error,
  };
}

export default function App() {
  const [messages, setMessages] = useState([WELCOME_MESSAGE]);
  const [draft, setDraft] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const [location, setLocation] = useState("");
  const [budget, setBudget] = useState("30");
  const [people, setPeople] = useState("3");
  const [days, setDays] = useState("3");

  function clearConversation() {
    setMessages([
      {
        ...WELCOME_MESSAGE,
        id: newId("welcome"),
      },
    ]);
  }

  async function handleSubmit() {
    const message = draft.trim();

    if (!message || isLoading) {
      return;
    }

    const sessionId = "sess-frontend-demo";
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
                label: location,
              },
            }
          : {}),
      });

      const content = extractAssistantContent(response.body?.events ?? []);

      setMessages((current) =>
        current.map((item) =>
          item.id === assistantMessageId
            ? { ...item, ...content }
            : item
        )
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
            : item
        )
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
