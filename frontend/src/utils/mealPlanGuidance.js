// Turns backend signals into actionable, user-facing suggestions about what
// to adjust (Budget, People, Days, Location) rather than just showing the
// raw error/notice text.
//
// Two sources of guidance:
//   1. Known error codes from CONTRACT-v1.md's error vocabulary — these are
//      explicit signals from the backend.
//   2. A client-side heuristic: if the message names a specific protein
//      (e.g. "chicken meal plan") but the returned plan doesn't contain it
//      anywhere, the backend currently returns a normal 200 with no
//      explanation (see: budget too low to fit chicken, so it silently
//      fell back to the cheapest valid — vegetarian — plan). This catches
//      that silently-substituted case.

const ERROR_GUIDANCE = {
  BUDGET_INFEASIBLE: "Try raising your budget, or lowering People or Days.",
  NO_DATA: "Try a different Location, or a more common item name.",
  STALE_DATA: "Prices may be out of date for this store — try a different Location.",
  UNSUPPORTED_EXCLUSION:
    "That dietary term isn't recognized yet — try common terms like vegetarian, vegan, or dairy-free.",
  GUARDRAIL_BLOCKED: "Try rephrasing your request.",
};

export function getErrorGuidance(error) {
  if (!error?.code) {
    return null;
  }
  return ERROR_GUIDANCE[error.code] ?? null;
}

// Common protein terms worth checking for. Not exhaustive — extend this
// list if you notice other requested ingredients going unfulfilled.
const PROTEIN_KEYWORDS = [
  "chicken",
  "beef",
  "pork",
  "lamb",
  "bacon",
  "ham",
  "sausage",
  "fish",
  "salmon",
  "tuna",
  "prawn",
  "prawns",
  "shrimp",
  "tofu",
];

const NEGATION_WORDS = [
  "no",
  "not",
  "without",
  "except",
  "excluding",
  "skip",
  "minus",
  "avoid",
];

function stripPunctuation(word) {
  return word.replace(/[^a-z]/g, "");
}

// Returns the first requested-but-missing protein keyword, or null.
export function findMissingRequestedIngredient(requestText, mealPlan) {
  if (!requestText || !mealPlan?.meals?.length) {
    return null;
  }

  const words = requestText.toLowerCase().split(/\s+/);

  const ingredientText = mealPlan.meals
    .flatMap((meal) => meal.ingredients ?? [])
    .map((ingredient) => (ingredient.item ?? "").toLowerCase())
    .join(" ");

  for (let i = 0; i < words.length; i += 1) {
    const word = stripPunctuation(words[i]);

    if (!PROTEIN_KEYWORDS.includes(word)) {
      continue;
    }

    const precedingWords = words
      .slice(Math.max(0, i - 2), i)
      .map(stripPunctuation);

    if (precedingWords.some((w) => NEGATION_WORDS.includes(w))) {
      continue;
    }

    if (!ingredientText.includes(word)) {
      return word;
    }
  }

  return null;
}

export function missingIngredientGuidance(keyword) {
  return `This plan doesn't include ${keyword} \u2014 it may not fit within the current Budget, People or Days. Try raising the budget, or lowering People/Days, if you'd like ${keyword} included.`;
}
