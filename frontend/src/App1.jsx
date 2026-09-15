import { useEffect, useState } from "react";
import "./App.css";
import BudgetControls from "./components/BudgetControls";
import ChatHeader from "./components/ChatHeader";
import Composer from "./components/Composer";
import MessageList from "./components/MessageList";
import { sendChat as sendChatMessage } from "./api/chatClient";

const REGION_OPTIONS = [
  { value: "albany", label: "Albany" },
  { value: "sylvia-park", label: "Sylvia Park" },
  { value: "devonport", label: "Devonport" }
];

const WELCOME_MESSAGE = {
  id: "welcome",
  role: "assistant",
  status: "complete",
  text: "Hello! I can compare grocery prices, create budget meal plans, and prepare a shopping list with sourced prices.",
  citations: {}
};

function newId(prefix) {
  if (globalThis.crypto?.randomUUID) {
    return `${prefix}-${globalThis.crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const SESSION_STORAGE_KEY = "grocery-session-id";
const HISTORY_STORAGE_KEY = "grocery-chat-history";

function loadStoredSessionId() {
  try {
    const existing = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (existing) return existing;

    const created = newId("sess");
    sessionStorage.setItem(SESSION_STORAGE_KEY, created);
    return created;
  } catch {
    return newId("sess");
  }
}

function storeSessionId(id) {
  try {
    sessionStorage.setItem(SESSION_STORAGE_KEY, id);
  } catch {
    // Storage is optional.
  }
}

function loadStoredMessages() {
  try {
    const raw = sessionStorage.getItem(HISTORY_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch {
    // Ignore invalid or unavailable storage.
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
      if (citation?.ref) citations[citation.ref] = citation;
    }

    if (event.type === "token" && event.text) {
      textParts.push(event.text);
    }

    if (event.type === "price_comparison") {
      const data = event.data ?? event.comparison ?? event;
      comparisons.push({
        ...data,
        options: data?.options ?? data?.items ?? []
      });
    }

    if (event.type === "meal_plan") {
      mealPlan = event.data ?? event.meal_plan ?? event;
    }

    if (event.type === "notice") notice = event.data ?? event;
    if (event.type === "no_data") noData = event.data ?? event;
    if (event.type === "error") error = event.data ?? event;
  }

  return {
    status: "complete",
    text: textParts.join(""),
    citations,
    comparisons,
    mealPlan,
    notice,
    noData,
    error
  };
}

function getSelectedRegion(location) {
  const value = location.trim().toLowerCase();
  if (!value) return null;

  return (
    REGION_OPTIONS.find(
      (region) =>
        region.value === value || region.label.toLowerCase() === value
    ) ?? { value, label: location.trim() }
  );
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

  useEffect(() => {
    try {
      sessionStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(messages));
    } catch {
      // Storage is optional.
    }
  }, [messages]);

  function clearConversation() {
    const freshSessionId = newId("sess");
    storeSessionId(freshSessionId);
    setSessionId(freshSessionId);
    setMessages([{ ...WELCOME_MESSAGE, id: newId("welcome") }]);
  }

  async function handleSubmit() {
    const message = draft.trim();
    if (!message || isLoading) return;

    const turnId = newId("turn");
    const selectedRegion = getSelectedRegion(location);
    const userMessage = {
      id: newId("user"),
      role: "user",
      status: "complete",
      text: message
    };
    const assistantMessageId = newId("assistant");
    const loadingAssistantMessage = {
      id: assistantMessageId,
      role: "assistant",
      status: "loading",
      text: "",
      citations: {}
    };

    const request = {
      version: "1.0",
      session_id: sessionId,
      turn_id: turnId,
      message,
      hints: {
        household_size: Number(people),
        budget_nzd: Number(budget),
        days: Number(days)
      }
    };

    if (selectedRegion) {
      request.location = {
        region: selectedRegion.value,
        label: selectedRegion.label
      };
    }

    console.log("Chat request:", request);

    setMessages((current) => [
      ...current,
      userMessage,
      loadingAssistantMessage
    ]);
    setDraft("");
    setIsLoading(true);

    try {
      const response = await sendChatMessage(request);
      const body = response?.body;

      if (!body?.events) {
        throw new Error(
          `The service returned an unexpected response (HTTP ${response?.status ?? "unknown"}).`
        );
      }

      const content = extractAssistantContent(body.events);

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
                    "Sorry, the grocery service could not be reached. Please try again."
                }
              }
            : item
        )
      );
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <>
      {/* Keep the existing JSX from the current file below this point. */}
      {/* Pass location, setLocation, REGION_OPTIONS, and handleSubmit to the existing controls. */}
      <ChatHeader onClear={clearConversation} />
      <BudgetControls
        location={location}
        setLocation={setLocation}
        budget={budget}
        setBudget={setBudget}
        people={people}
        setPeople={setPeople}
        days={days}
        setDays={setDays}
        regionOptions={REGION_OPTIONS}
      />
      <MessageList messages={messages} />
      <Composer
        value={draft}
        onChange={setDraft}
        onSubmit={handleSubmit}
        disabled={isLoading}
      />
    </>
  );
}
