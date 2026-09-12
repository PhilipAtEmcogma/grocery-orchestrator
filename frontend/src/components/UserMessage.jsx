export default function UserMessage({ text }) {
  return (
    <article className="message-row message-row--user">
      <div className="message-bubble message-bubble--user">
        <p className="message-label">You</p>
        <p className="message-text">{text}</p>
      </div>
    </article>
  );
}
