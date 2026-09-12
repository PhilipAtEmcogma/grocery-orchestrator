import { useEffect, useRef } from "react";
import AssistantMessage from "./AssistantMessage";
import UserMessage from "./UserMessage";

export default function MessageList({ messages }) {
  const endOfMessagesRef = useRef(null);

  useEffect(() => {
    endOfMessagesRef.current?.scrollIntoView({
      behavior: "smooth",
      block: "end",
    });
  }, [messages]);

  return (
    <main className="message-list" aria-label="Conversation" aria-live="polite">
      {messages.map((message) => {
        if (message.role === "user") {
          return <UserMessage key={message.id} text={message.text} />;
        }

        return (
          <AssistantMessage
            key={message.id}
            text={message.text}
            status={message.status}
            comparisons={message.comparisons ?? []}
            mealPlan={message.mealPlan}
            citations={message.citations ?? {}}
            notice={message.notice}
            noData={message.noData}
            error={message.error}
          />
        );
      })}

      <div ref={endOfMessagesRef} />
    </main>
  );
}
