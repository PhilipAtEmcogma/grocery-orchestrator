import { useEffect, useRef } from "react";
import AssistantMessage from "./AssistantMessage";
import UserMessage from "./UserMessage";

export default function MessageList({ messages }) {
  // Ref per message DOM node, keyed by message id, so we can scroll a
  // specific message into view rather than always jumping to the bottom.
  const messageNodesRef = useRef(new Map());
  const previousCountRef = useRef(0);

  useEffect(() => {
    const previousCount = previousCountRef.current;
    previousCountRef.current = messages.length;

    // Only scroll when a message was actually added (a new user turn, or
    // the assistant's reply starting). Content updates to an *existing*
    // message — e.g. the loading placeholder filling in with a long meal
    // plan — keep the same message count, so we deliberately don't
    // re-scroll then. Otherwise a long response drags the view down to
    // its own bottom right as it finishes loading, hiding the intro text
    // and citations at the top of what the person is trying to read.
    if (messages.length <= previousCount) {
      return;
    }

    const latestMessage = messages[messages.length - 1];
    const node = messageNodesRef.current.get(latestMessage.id);

    node?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  }, [messages]);

  function registerMessageNode(id) {
    return (node) => {
      if (node) {
        messageNodesRef.current.set(id, node);
      } else {
        messageNodesRef.current.delete(id);
      }
    };
  }

  return (
    <main className="message-list" aria-label="Conversation" aria-live="polite">
      {messages.map((message, index) => {
        if (message.role === "user") {
          return (
            <div key={message.id} ref={registerMessageNode(message.id)}>
              <UserMessage text={message.text} />
            </div>
          );
        }

        const precedingMessage = messages[index - 1];
        const requestText =
          precedingMessage?.role === "user" ? precedingMessage.text : "";

        return (
          <div key={message.id} ref={registerMessageNode(message.id)}>
            <AssistantMessage
              text={message.text}
              status={message.status}
              comparisons={message.comparisons ?? []}
              mealPlan={message.mealPlan}
              citations={message.citations ?? {}}
              notice={message.notice}
              noData={message.noData}
              error={message.error}
              requestText={requestText}
            />
          </div>
        );
      })}
    </main>
  );
}
