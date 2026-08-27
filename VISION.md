# Project Create Food (AutoEat)

A personal project to offload food decision-making, procurement, and preparation
so that the operator spends near-zero daily attention on eating well.

This folder documents the honest, scoped version of that idea: what is buildable
within twelve months, what is not, the general architecture, a detailed spec for
the first subsystem (the planner), and a serious treatment of the moral
implications of building it — and of turning it into a company.

## The one-paragraph thesis

"Automate my food" is not one project. It is three, with wildly different
difficulty. Menu and macro **planning** is a pure software problem, solvable this
year. **Procurement** is solvable this year *only if you drop the idea of a robot
physically shopping in a store* and instead automate the order through delivery
and pickup channels. **Cooking and prep** is the real frontier: full autonomous
cooking of arbitrary meals in a normal kitchen is beyond the current state of the
art for anyone, but a single constrained dish, prepared in a purpose-built rig
with no open flame or blades in version one, is genuinely achievable and would be
a legitimately impressive result.

## What's in this folder

- `01_Plan_and_Timeline.md` — the scoped twelve-month plan, milestones, hardware,
  costs, risks, and the solo-vs-funded question.
- `02_Architecture.md` — the general system architecture across all three layers
  plus the learning loop and the safety subsystem.
- `03_Layer1_Planner.md` — a detailed spec for the planner, which is the piece you
  can start building immediately.
- `04_Moral_Implications.md` — job displacement, the "someone else will build it"
  argument, the slopification concern, and the difference between building this
  for yourself and deploying it to replace millions of workers.

## The single most important framing

Every hard part you were most excited about is a part to cut, at least in v1. The
store robot and the general-purpose kitchen chef are exactly the pieces that would
sink the project. What's left after cutting them is still a startlingly large
chunk of the dream, and it is buildable.
