import MealPlanCard from "./MealPlanCard";
import PriceComparisonCard from "./PriceComparisonCard";

export default function AssistantMessage({
  text,
  status,
  comparisons,
  mealPlan,
  citations,
  notice,
  noData,
  error,
}) {
  const visibleText = error?.message ?? noData?.message ?? text;

  return (
    <article className="message-row message-row--assistant">
      <div className="assistant-avatar" aria-hidden="true">✦</div>

      <div className="message-bubble message-bubble--assistant">
        <p className="message-label">Smart Grocery Assistant</p>

        {status === "loading" && (
          <div className="typing-indicator" role="status" aria-live="polite">
            <span>Preparing your answer</span>
            <span className="typing-dots" aria-hidden="true">•••</span>
          </div>
        )}

        {visibleText && <p className="message-text">{visibleText}</p>}

        {notice && (
          <p className="notice-message">
            {notice.message ?? notice.text ?? String(notice)}
          </p>
        )}

        {comparisons?.map((comparison, index) => (
  <PriceComparisonCard
    key={`${comparison.query_item ?? "comparison"}-${index}`}
    comparison={comparison}
    citations={citations}
  />
))}

        {mealPlan && (
          <MealPlanCard
            mealPlan={mealPlan}
            citations={citations}
          />
        )}
      </div>
    </article>
  );
}
