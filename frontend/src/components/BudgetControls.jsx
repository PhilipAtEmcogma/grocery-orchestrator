import { NZ_LOCATIONS } from "../data/nzLocations";

export default function BudgetControls({
  location,
  budget,
  people,
  days,
  onLocationChange,
  onBudgetChange,
  onPeopleChange,
  onDaysChange,
}) {
  const budgetNum = Number(budget);
  const peopleNum = Number(people);
  const daysNum = Number(days);

  const perPersonPerDay =
    budgetNum > 0 && peopleNum > 0 && daysNum > 0
      ? budgetNum / peopleNum / daysNum
      : null;

  // Rough heuristic, not a backend signal: under ~$5/person/day tends to
  // rule out pricier proteins like chicken even when plenty is technically
  // affordable overall.
  const isTightBudget = perPersonPerDay !== null && perPersonPerDay < 5;

  return (
    <section className="budget-controls" aria-label="Meal planning preferences">
      <label>
        <span>Location</span>
        <input
          type="text"
          value={location}
          onChange={(event) => onLocationChange(event.target.value)}
          placeholder="Optional suburb or store"
          list="nz-locations"
          autoComplete="off"
        />
        <datalist id="nz-locations">
          {NZ_LOCATIONS.map((suburb) => (
            <option key={suburb} value={suburb} />
          ))}
        </datalist>
      </label>

      <label>
        <span>Budget (NZD)</span>
        <input
          type="number"
          min="1"
          step="1"
          value={budget}
          onChange={(event) => onBudgetChange(event.target.value)}
        />
      </label>

      <label>
        <span>People</span>
        <input
          type="number"
          min="1"
          step="1"
          value={people}
          onChange={(event) => onPeopleChange(event.target.value)}
        />
      </label>

      <label>
        <span>Days</span>
        <input
          type="number"
          min="1"
          step="1"
          value={days}
          onChange={(event) => onDaysChange(event.target.value)}
        />
      </label>

      <p className="controls-tip">
        {isTightBudget
          ? `That's about $${perPersonPerDay.toFixed(2)}/person/day \u2014 specific ingredient requests (like chicken) may not fit. Try raising Budget, or lowering People/Days, for more variety.`
          : "Results are cheapest-first. If a specific ingredient doesn't show up, try raising the budget or adjusting People/Days."}
      </p>
    </section>
  );
}
