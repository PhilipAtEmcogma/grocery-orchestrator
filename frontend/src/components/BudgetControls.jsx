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
  return (
    <section className="budget-controls" aria-label="Meal planning preferences">
      <label>
        <span>Location</span>
        <input
          type="text"
          value={location}
          onChange={(event) => onLocationChange(event.target.value)}
          placeholder="Optional suburb or store"
        />
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
    </section>
  );
}
