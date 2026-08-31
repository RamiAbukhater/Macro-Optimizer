# AutoEat — Scoped Twelve-Month Plan

## Goal restated honestly

Offload as much food cognition and logistics as possible, and physically prepare
a small, fixed set of meals, so that on a normal day the operator makes zero food
decisions and does zero prep. The end state is not a humanoid chef. It is a
pipeline that plans, orders, and prepares — where "prepares" means one or a few
constrained dishes done reliably and safely.

## The three layers and their real difficulty

**Layer 1 — Planning (menus + macros).** Difficulty: low. This is pure software.
Generating a weekly menu against macro targets, health goals, dietary
constraints, budget, and pantry state is a weekend-to-a-month build with a modern
LLM and a nutrition database. Treat this as effectively solved and build it first
— it also produces the structured output the other two layers consume.

**Layer 2 — Procurement (getting the ingredients).** Difficulty: low-to-medium,
*only if the physical store robot is abandoned.* Sending a robot into a store
(Ralphs, etc.) to navigate around people, pick hundreds of visually varied items,
and check out is beyond frontier robotics AND a regulatory, insurance, and
liability wall a small team will not clear in a year. It is, in fact, the single
hardest thing in the whole vision — harder than the cooking. Grocery is already
solved digitally: automate the *order* through delivery/pickup channels, not the
trip.

**Layer 3 — Cooking / prep.** Difficulty: high; this is the frontier. Full
autonomous cooking of arbitrary meals in an unmodified kitchen is not achievable
this year by anyone. A single constrained dish in a rig you fully control is. The
way real food robots ship (salad/bowl assembly systems, fry-station robots) is by
massively constraining the environment: pre-portioned ingredients in dispensers,
fixed stations, purpose-built end effectors, one narrow job. Version one avoids
open flame and blades entirely so the worst failure mode is a dropped ingredient,
not a fire or a cut.

## The single-dish target

Selection criteria for the first dish: structured, forgiving of small errors,
no knife or open flame, repeatable, and macro-relevant.

Recommended v1 target: an **assembled bowl** — a grain or base, a pre-cooked
protein, vegetables, and a sauce, dispensed and combined from fixed stations. This
is mostly pick-and-place and controlled pour, which is the most tractable
manipulation regime. A close second is a **sealed-cooker dish** (rice-cooker or
sous-vide-style), where the robot loads pre-portioned ingredients into a covered,
temperature-controlled appliance and starts a program — the appliance owns the
heat, so the robot never handles fire.

Deliberately deferred to later versions: anything requiring cutting, frying,
flipping, flame, or judgment about doneness by sight.

## Timeline

**Months 0–1 — Foundations.**
Build the Layer 1 planner MVP (menu + macros + shopping list output). Choose the
single dish. Design the prep-cell layout on paper. Order hardware. Stand up the
procurement integration against a delivery/pickup channel in test mode.

**Months 1–3 — Data rig and procurement live.**
Assemble the teleoperation rig (arm + dispensers + appliance + cameras). Begin
collecting human teleop demonstrations of the single dish. Make procurement place
real orders end to end from the planner's shopping list.

**Months 3–6 — First policy.**
Train an imitation-learning policy on the collected demos. Stand up a simulation
of the prep cell for data augmentation (not as the primary trainer — for
manipulation, real demos usually beat pure sim). Build the safety subsystem:
e-stop, mechanical limits, thermal/interlock sensors, monitoring.

**Months 6–9 — Close the loop.**
Iterate policy on the real rig; every failure tells you where sim or training
lied, so fix and retrain. Integrate the three layers so a menu becomes an order
becomes a prepped dish with no human in the middle. Start measuring reliability
(success rate over N attempts).

**Months 9–12 — Harden and demo.**
Push reliability up, validate safety under adversarial conditions, and produce the
end-to-end demonstration: the system plans a day's meals, orders the ingredients,
and physically prepares the target dish, unattended, repeatably.

## Rough cost picture

The largest swing is Layer 3 hardware. Low-cost open manipulation arms and an
open training stack put a real teleop-and-imitation setup in the low thousands of
dollars rather than the millions people assume. Dispensers, a controlled
appliance, cameras, and a workstation GPU add to that. A serious but frugal v1
prep cell is plausibly low-thousands to low-tens-of-thousands depending on
ambition and how much you fabricate yourself. (These figures move quickly; get
current arm/platform prices before committing.)

## Solo or funded?

Layers 1 and 2 are a solo, evenings-and-weekends effort. Layer 3 is where this
becomes a real R&D project: manipulation reliability, sim-to-real iteration, and
safety validation are the kind of work that benefits from a small team, a
dedicated space, and either savings or funding. A sensible path is to build
Layers 1–2 solo, prove them useful to yourself, and use that working pipeline plus
a single-dish prototype as the thing that justifies (to yourself or an investor)
committing to Layer 3.

## Top risks

- **Manipulation reliability.** The gap between a demo that works once and a system
  that works every day is the whole game.
- **Sim-to-real for contact-rich tasks.** Cooking is where simulation lies to you
  most; budget for real-robot iteration, do not expect train-in-sim-then-ship.
- **Safety and liability.** A machine near heat or handling food carries real
  physical and food-safety risk even for personal use.
- **Procurement access.** Automated ordering must respect the terms of service of
  whatever channel you use; API access and policy can change.
- **Scope creep.** The gravity toward "but what if it also..." is strong. The
  single-dish discipline is what keeps this finishable.
