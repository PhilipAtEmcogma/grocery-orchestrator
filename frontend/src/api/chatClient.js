const API_URL =
  import.meta.env.VITE_API_URL || "http://localhost:8000/chat";

export async function sendChat(request) {
  const response = await fetch(API_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  let body;

  try {
    body = await response.json();
  } catch {
    throw new Error("The server returned an unreadable response.");
  }

  return {
    status: response.status,
    body,
  };
}