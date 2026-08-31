# Handoff Brief — Feasibility Assessment of Project AutoEat

## To the agent receiving this file

You are being asked to independently assess the feasibility of the project
described below and to produce a written **feasibility report**. Treat this brief as
your primary context; you should not assume access to any prior conversation. The
supporting files listed in "Reference material" sit in the same folder and expand on
each area — read them before writing.

Your job is not to cheerlead and not to dismiss. It is to give an honest, evidence-
based judgment of whether this can be built, at this budget, in this timeframe, by
this kind of team — and to say clearly where it can't. Verify claims against current
sources where you can; flag anything you could not verify.

---

## 1. Project snapshot

**AutoEat** is a personal/student robotics project with a long-term vision of
offloading food planning, procurement, and preparation. The scoped near-term goal
(the thing to assess) is a **single-task food-prep robot**: a fixed-base robotic arm
that prepares **one constrained "cold-assembly" dish** (no cooking, no heat, no
blades in v1 — e.g. overnight oats or an assembled grain/yogurt bowl) by manipulating
a spoon and light pre-portioned ingredient cups, learned via imitation learning.

The full three-layer vision (software menu/macro **planner**, automated
**procurement/ordering**, physical **prep**) is documented separately, but the
feasibility question centers on the **physical prep robot (Layer 3)** at v1 scope.

## 2. Key decisions already made (do not re-litigate unless you find them wrong)

- **Path A, not Path B.** The robot never lifts heavy items. Pots/pans (if ever used)
  stay fixed; the robot only manipulates objects under ~400 g (spoon, light cups,
  light bowls). This is deliberate to stay on a cheap arm and keep failures benign.
  Assess whether this constraint actually holds for a real dish.
- **Hardware base: LeRobot SO-101 arm.** 6-DOF, ~500 mm reach, ~500 g payload,
  open-source 3D-printed frame, Feetech STS3215 smart servos (~$13–15 each, position
  feedback). Frame printed free at a university makerspace; only motors/electronics
  are bought.
- **Learning approach: imitation learning** (e.g. ACT) via the Hugging Face LeRobot
  stack, trained on **Google Colab free GPU** or a university cluster. Demonstrations
  collected either by a leader+follower teleop pair or by kinesthetic teaching
  (moving the follower by hand). Datasets/checkpoints stored on ~400 GB Google Drive.
- **Perception:** ordinary RGB USB webcams (one fixed overhead/scene, one at the
  wrist). Depth camera (RealSense) treated as optional, not required. Note: the scene
  camera must be locked rigid — it may be adjustable at setup, but must not move
  during or between data collection and deployment.
- **Sensing:** an MLX90614 non-contact IR thermometer used for monitoring/safety
  only, not primary control. Not critical for a cold-assembly v1.
- **Budget:** ~$269 for a single-arm build (kinesthetic teaching) or ~$393 for a
  leader+follower pair, assuming free makerspace printing. See the cost sheet.
- **Timeline:** twelve months, structured in the plan doc.
- **Team:** student-scale, plausibly a fourth-year capstone team, with a robotics
  professor as sponsor/advisor and potential access to lab hardware, printers, and
  GPUs.

## 3. Reference material (in this folder)

- `01_Plan_and_Timeline.md` — the scoped 12-month plan, milestones, risks.
- `02_Architecture.md` — system architecture across all three layers + learning +
  safety subsystems.
- `03_Layer1_Planner.md` — the software menu/macro planner spec.
- `05_Hardware.md` — payload analysis, the Path A/B fork, parts, and the student
  minimal-budget build.
- `AutoEat_Cost_Sheet.xlsx` — itemized bill of materials with live totals.
- `04_Moral_Implications.md` — ethics/labor context (not central to technical
  feasibility, but relevant if you assess the company path).

## 4. What "feasibility" means here — assess each dimension

1. **Technical feasibility (highest priority).**
   - Can a ~500 g-payload SO-101 reliably manipulate a wooden spoon and light cups to
     assemble one cold dish? Where does contact-rich manipulation realistically break?
   - Is imitation learning on this hardware, trained on free/cheap compute, sufficient
     to reach usable reliability for one narrow task? What success rate is realistic,
     and how many demonstrations would it take?
   - Is the single-fixed-camera + wrist-camera perception setup adequate?
   - What is the honest hardest sub-problem, and is it a known-solved pattern in the
     LeRobot / low-cost-manipulation community or an open research risk?
2. **Financial feasibility.** Is the ~$269–393 budget realistic, or are there missing
   costs (spares, failed prints, filament, incidental tools, shipping)? What's the
   true expected spend with a 20–30% contingency?
3. **Timeline feasibility.** Is twelve months realistic for a student team (part-time,
   coursework alongside) to go from parts to a reliably-demoable single dish? Where is
   the schedule most likely to slip?
4. **Team/skill feasibility.** What skills does the team need (CAD, embedded, ML,
   mechanical assembly), and is a fourth-year team plausibly capable? What's the
   steepest learning curve?
5. **Safety.** Even cold-assembly: pinch points, food safety/hygiene, unattended
   operation. Is the "no heat, no blades, nothing over 400 g" envelope sufficient for
   safe v1 operation?
6. **IP / institutional (brief).** Note the university-IP consideration if lab
   resources or faculty involvement are used, and flag it as something to clarify —
   you do not need to resolve it.

## 5. Assumptions to test (do not take these on faith)

- That makerspace printing is actually free/available to this team.
- That the SO-101's ~500 g payload and ~500 mm reach cover the real object set and
  workspace layout for a chosen dish.
- That free-tier cloud compute is sufficient to train the policy (time limits,
  session caps).
- That kinesthetic teaching produces usable demonstration data, if the team skips the
  leader arm to save money.
- That a "cold-assembly" dish exists that is both nutritionally worthwhile and simple
  enough to be the realistic first target. Suggest the single best candidate dish.

## 6. Requested output — the feasibility report

Produce a structured report (Markdown or Word) containing:

1. **Executive summary** — a one-paragraph overall verdict.
2. **Overall feasibility rating** — e.g. Feasible / Feasible-with-caveats / High-risk /
   Infeasible — with an explicit confidence level.
3. **Dimension-by-dimension assessment** — one section per dimension in Section 4,
   each with a rating and the evidence behind it.
4. **The single biggest risk** and what would most reduce it.
5. **Assumption audit** — for each item in Section 5, state verified / unverified /
   false, with sources.
6. **Recommended go/no-go and next concrete step** — if "go," the first thing the
   team should buy or build; if "no-go," what would have to change.
7. **Recommended first-target dish**, with a one-line justification.
8. **Sources** — cite current, real references for every external claim (hardware
   specs, prices, method viability). Prefer primary/manufacturer/official-docs
   sources. Explicitly flag anything you could not verify.

## 7. Ground rules

- Be critical and specific. "It depends" is only acceptable with the dependency named.
- Distinguish *engineering risk* (hard but known how) from *research risk* (nobody has
  cleanly solved it). The reader needs to know which is which.
- Where numbers matter (payload, price, success rate, demo count, training time), give
  ranges with reasoning, not single guesses.
- If any decision in Section 2 looks wrong, say so and explain — those are current
  choices, not constraints you must honor.
- Keep the reader's context in mind: a student on a ~$400 budget deciding whether to
  commit a year. The report should leave them able to make that call.
