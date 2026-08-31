# AutoEat — System Architecture

The system is a pipeline of three functional layers sitting on top of two
cross-cutting subsystems (learning and safety), coordinated by an orchestrator.

```
                          ┌─────────────────────────────┐
                          │        ORCHESTRATOR         │
                          │  scheduling · inventory ·   │
                          │  state · triggering · logs  │
                          └──────────────┬──────────────┘
                                         │
        ┌────────────────────────────────┼────────────────────────────────┐
        │                                │                                 │
        ▼                                ▼                                 ▼
┌───────────────┐              ┌───────────────────┐              ┌──────────────────┐
│  LAYER 1      │   menu +     │  LAYER 2          │  order       │  LAYER 3         │
│  PLANNER      │──shopping──▶ │  PROCUREMENT      │──confirmed──▶│  PREP CELL       │
│               │  list        │                   │  inventory   │                  │
│ LLM + macro   │              │ catalog mapping · │              │ policy runtime · │
│ optimizer +   │              │ delivery/pickup   │              │ arm + dispensers │
│ nutrition DB  │              │ ordering API      │              │ + sealed cooker  │
└───────────────┘              └───────────────────┘              └────────┬─────────┘
        ▲                                                                    │
        │ feedback (ratings, adherence)                                      │
        └────────────────────────────────────────────────────────────────────┘
                                         ▲                                   │
                          ┌──────────────┴───────────┐        ┌─────────────┴───────────┐
                          │   LEARNING SUBSYSTEM     │        │    SAFETY SUBSYSTEM      │
                          │ teleop capture · imitation│       │ e-stop · limits ·        │
                          │ learning · sim augment ·  │       │ interlocks · thermal ·   │
                          │ real-robot iteration      │       │ monitoring · watchdog    │
                          └───────────────────────────┘        └──────────────────────────┘
```

## Layer 1 — Planner (see 03_Layer1_Planner.md for detail)

A service that turns a user profile and goals into a structured weekly menu, macro
targets per meal, and a consolidated shopping list. Pure software. Outputs a
machine-readable plan (JSON) that Layers 2 and 3 consume, plus human-readable
recipe/meal cards. Learns from feedback: meal ratings and adherence adjust future
menus.

## Layer 2 — Procurement

Consumes the shopping list and turns it into a real order.

- **Catalog mapping.** Resolve each abstract line item ("200g rolled oats") to a
  concrete purchasable product in a grocery channel's catalog, honoring pantry
  state so you don't re-buy what you have.
- **Order placement.** Place a delivery or pickup order through a supported
  channel's ordering integration, within its terms of service. No physical robot
  in a store — this is deliberately a software boundary.
- **Inventory reconciliation.** On delivery, update the pantry/inventory model so
  the orchestrator and planner know what is actually on hand.

## Layer 3 — Prep cell

A purpose-built, environment-constrained station, not a general kitchen.

- **Fixed stations and dispensers.** Pre-portioned or bulk ingredients in known
  positions so perception and manipulation face a bounded problem.
- **Manipulator.** A low-cost arm (or bimanual pair) with a task-appropriate end
  effector — panel/parallel grippers and scoops rather than a dexterous humanoid
  hand. Match the effector to the dish, not to a general fantasy.
- **Sealed cooking appliance.** A covered, temperature-controlled cooker (induction
  + closed vessel, rice-cooker, or sous-vide) that owns all heat, so the robot
  loads and starts a program but never handles flame.
- **Perception.** Fixed overhead and side cameras; optionally tactile sensing on
  the effector for contact-rich steps.
- **Policy runtime.** Executes the learned manipulation policy for the target dish,
  gated at every step by the safety subsystem.

## Learning subsystem

- **Teleoperation capture.** A human drives the rig through the target dish many
  times, recording synchronized observations and actions — the demonstration
  dataset.
- **Imitation learning.** Train a policy to reproduce the demonstrated behavior.
  For contact-rich manipulation this is currently the most reliable path and
  generally outperforms pure sim-to-real.
- **Simulation augmentation.** A physics simulation of the cell for scaling data
  and randomizing conditions — used to *augment*, not to replace real data. Expect
  simulation to be least trustworthy exactly where cooking is hardest (deformables,
  liquids, thermal, contact).
- **Real-robot iteration.** The loop that actually closes the sim-to-real gap: run
  on real hardware, find where the model was wrong, fix sim/training, retrain. This
  is continuous, not a final test step.

## Safety subsystem

Cross-cutting and authoritative — it can veto or halt any action.

- Hardware e-stop and mechanical workspace limits.
- Interlocks so the appliance cannot run in an unsafe state (lid open, no vessel,
  over-temperature).
- Thermal and presence sensing; a watchdog that halts on anomaly or loss of
  perception.
- Design bias toward failure modes that are benign: no blades, no open flame in
  v1, so the worst realistic outcome is a spill or a dropped ingredient.
- Food safety: temperature and time handling for anything perishable, and
  allergen/cross-contact discipline enforced in the plan and the physical layout.

## Orchestrator

The conductor. Holds system state (menu, inventory, schedule), decides when to
order and when to prep, triggers each layer, logs everything for debugging and for
the learning loop, and surfaces status and failures to the operator.
