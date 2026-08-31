# AutoEat — Layer 1: The Planner

This is the piece to build first. It is pure software, it is achievable in weeks,
and it produces the structured output every other layer depends on. Building it now
also forces you to define the data contracts for the whole system.

## What it does

Turn a person's profile, goals, and constraints into:

1. A **weekly menu** — specific meals per day.
2. **Macro and calorie targets** per meal and per day.
3. A consolidated **shopping list**, deduplicated against current pantry state.
4. Human-readable **meal cards** (ingredients, quantities, prep steps).

And then improve over time from feedback.

## Inputs

- **Person model.** Biometrics (age, sex, weight, height, activity level), goals
  (cut, maintain, gain, performance, specific health markers), and any clinician
  guidance. Used to derive calorie and macro targets.
- **Dietary constraints.** Allergies and intolerances (hard constraints, never
  violated), and preferences/dislikes (soft constraints, penalized not forbidden).
- **Lifestyle constraints.** Budget, time available for eating, number of distinct
  meals tolerable per week (variety vs. batching), and which meals the prep cell
  can actually make (this couples Layer 1 to Layer 3's capabilities).
- **Pantry/inventory state.** What is already on hand, fed back from Layer 2.
- **Health trends input.** A curated, sourced notion of current nutrition guidance
  — treated as an updatable knowledge input, not as gospel, and never as medical
  advice.

## Core logic

1. **Target derivation.** Compute daily energy needs and macro split from the
   person model and goal. This is standard nutrition math (an estimated energy
   expenditure plus a goal-based adjustment, then a protein/fat/carb split).
2. **Candidate generation.** An LLM proposes meals that fit constraints and
   preferences and hit variety goals, drawing on a recipe corpus. The LLM is good
   at variety, substitution, and "make it interesting"; it is *not* trusted for the
   numbers.
3. **Macro optimization / verification.** Every proposed meal's macros are computed
   from a structured nutrition database (not from the LLM's guess), and the day/week
   is checked against targets. If it misses, adjust portions or swap items and
   re-verify. This separation — LLM for creativity, database + solver for the
   numbers — is what keeps the plan trustworthy.
4. **Shopping list assembly.** Aggregate ingredient quantities across the menu,
   subtract pantry state, and emit a clean purchasable list for Layer 2.

## Outputs (data contracts)

- `weekly_plan.json` — days → meals → { items, quantities, macros, prep steps,
  which subsystem prepares it }. This is the canonical object the orchestrator and
  Layers 2/3 read.
- `shopping_list.json` — deduplicated line items with quantities and pantry-aware
  net amounts.
- Meal cards — rendered human-readable views for when a human is in the loop.

## Feedback loop

- **Ratings.** The operator rates meals; low scores penalize similar candidates.
- **Adherence.** Track what was actually eaten vs. planned; systematic deviations
  reshape future plans.
- **Outcome signals (optional, careful).** If the person tracks weight or health
  markers, feed trends back to adjust targets — with explicit guardrails so the
  system nudges gently and never chases a number in an unhealthy direction.

## Guardrails

- Allergies and medical restrictions are hard constraints, enforced structurally,
  never left to the LLM's discretion.
- The planner presents nutrition information and plans; it does not give medical
  advice, and it should defer to a clinician for anything therapeutic.
- Avoid designs that could reinforce disordered eating: no extreme deficits, floors
  on calories and key nutrients, and no "chase the scale" behavior.
- All numeric claims trace to the nutrition database, so they are auditable.

## Why build this first

It is the fast win, it is useful on its own (a fully automated meal planner and
grocery-ordering pipeline is already a real quality-of-life tool before any robot
exists), and it defines the interfaces that keep the rest of the project honest.
If Layer 1 plus Layer 2 running for a few months genuinely removes food-thinking
from your day, you will have learned a great deal about whether Layer 3 is worth
the much larger investment.
