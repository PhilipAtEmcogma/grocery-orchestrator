import MealPlanCard from "./MealPlanCard";
import PriceComparisonCard from "./PriceComparisonCard";
import {
  getErrorGuidance,
  getNoDataGuidance,
  findMissingRequestedIngredient,
  missingIngredientGuidance,
} from "../utils/mealPlanGuidance";

export default function AssistantMessage({
  text,
  status,
  comparisons,
  mealPlan,
  citations,
  notice,
  noData,
  error,
  requestText,
}) {
  const visibleText = error?.message ?? noData?.message ?? text;
  const errorGuidance = getErrorGuidance(error);
  const noDataGuidance = !error ? getNoDataGuidance(noData) : null;

  // Only run the missing-ingredient check on a successful, complete plan —
  // an error already has its own guidance above.
  const missingKeyword =
    !error && mealPlan
      ? findMissingRequestedIngredient(requestText, mealPlan)
      : null;

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

        {errorGuidance && (
          <p className="guidance-banner">💡 {errorGuidance}</p>
        )}

        {noDataGuidance && (
          <p className="guidance-banner">💡 {noDataGuidance}</p>
        )}

        {missingKeyword && (
          <p className="guidance-banner">
            💡 {missingIngredientGuidance(missingKeyword)}
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
