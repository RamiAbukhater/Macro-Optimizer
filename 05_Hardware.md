# AutoEat — Hardware Considerations

This document turns the "grab spoons, bowls, pots, and pans, with a camera and a
thermometer" requirement into concrete parts, costs, and — most importantly — the
one design decision those requirements force on you before anything else.

All prices are approximate, US, and current as of August 2026. They move; re-check
before buying.

## The one number that decides the whole design: payload

Everything about cost, safety, and difficulty flows from how heavy the objects are
that the arm has to lift. Here is the uncomfortable reality of your object list:

| Object | Rough weight |
|---|---|
| Wooden spoon | 50–100 g |
| Empty light mixing bowl | 150–400 g |
| Empty stainless pot (small) | 700 g – 1.5 kg |
| Empty skillet / cast-iron pan | 1 – 2.5+ kg |
| **Pot or pan with food/water in it** | **2 – 6+ kg** |

Now compare that to what affordable arms can actually lift:

| Arm | DOF | Payload | Approx. price | Notes |
|---|---|---|---|---|
| SO-101 (LeRobot) | 6 | ~500 g | ~$300 DIY / $499 kit | Feetech STS3215 servos, ~500 mm reach. The entry point for imitation learning. |
| ViperX 300 S (ALOHA arm) | 6 | ~750 g | several $k per arm | ALOHA setups use two of these for bimanual work. |
| UFACTORY xArm 6 | 6 | ~5 kg | ~$8,800 | A real cobot; can actually lift a loaded pot. Different budget and safety class entirely. |

Read those two tables together and the conclusion is unavoidable: **a wooden spoon
and a light empty bowl are well within a cheap arm's reach; a pot or a pan — and
absolutely a full one — is not, by a factor of four to ten.** The requirement to
"grab pots and pans" is not a small add-on. It is the thing that, taken literally,
throws you out of the hobby-arm tier and into an $8,000+ cobot with a much bigger
safety envelope.

So the first real design decision is this fork.

## The fork

**Path A — Never lift the heavy things (recommended for v1).**
Redesign the task so the pot or pan *stays put* and the robot only manipulates light
objects. The vessel sits fixed on or in a controlled cooking appliance; the robot
grabs the *wooden spoon* to stir inside the fixed pot, and picks up light
pre-portioned ingredient cups and light bowls. The heavy, hot, dangerous object
never moves under robot power. This keeps you on a ~$500 arm, keeps the failure
modes benign (a dropped spoon, not a dropped pot of boiling water), and is exactly
how commercial food robots dodge this problem. You lose the ability to, say, pour a
pan out — but you gain a project that is actually buildable and safe this year.

**Path B — Actually lift pots and pans.**
Now you need a ~5 kg-class cobot (xArm 6 or similar, ~$8–15k with gripper), a
sturdy mounting frame, and a serious safety design because a 5 kg arm moving a hot,
heavy, liquid-filled vessel is genuinely hazardous. This is a fundable-company-scale
build, not a weekend one. It is a legitimate v2+ goal, but leading with it will
consume your budget and your safety-engineering time before you have proven you can
even reliably stir.

My strong recommendation: **build Path A first.** Design the whole v1 so nothing
heavier than ~400 g ever leaves the table. It is the difference between a project
you finish and one you fund.

## End effector (gripper)

Your objects have very different grip geometries, and one simple gripper can cover
the light ones:

- **Wooden spoon handle:** a round-ish rod ~1.5–2.5 cm across. A parallel-jaw
  gripper with a shallow V-groove or slightly compliant (rubber-lined) fingertips
  holds this reliably. This is the workhorse grasp.
- **Bowl rim / ingredient cup:** a parallel gripper pinching the rim, or a simple
  scoop end effector for pre-portioned dry goods.
- The cheap arms ship *with* a parallel gripper, so v1 needs no custom effector —
  just printed or lined fingertips shaped for the spoon handle and cup rims.

Optional upgrade: cheap tactile/force sensing on the fingertips so the robot knows
it actually has the spoon before it tries to stir. Not required for v1, valuable
later.

## Perception (camera)

- **Primary: a depth camera.** An Intel RealSense D435 / D435i (~$300–350) gives you
  color plus depth, which makes grasping and locating objects far more robust than a
  flat webcam. Mount one **overhead** looking down at the workspace for the global
  scene.
- **Secondary: a wrist camera.** A cheap USB webcam (~$20–60) mounted near the
  gripper gives a close-up view for the moment of grasping. ALOHA-style setups use
  exactly this combination (overhead + wrist), and it is well supported in the
  LeRobot software stack.
- **Lighting matters.** Fixed, even lighting over the cell removes a whole class of
  perception failures for almost no money. Do not skip it.

## Temperature sensing (thermometer)

Two honest options, for two different jobs:

- **Non-contact IR sensor (e.g. MLX90614, ~$10–20).** Reads surface temperature from
  a distance over I2C. Great for "is the pan surface hot," "has the food reached a
  rough target," and safety interlocks ("something here is too hot to touch"). Cheap
  and easy to wire to a microcontroller. Accuracy is approximate and surface-only.
- **Probe thermocouple (~$10–30 for the sensor + amplifier).** For true internal food
  temperature (food-safety relevant — is the chicken actually at a safe internal
  temp). More accurate for the thing that matters for safety, but requires physically
  inserting the probe, which is its own manipulation problem.
- **Best v1 move:** let the *appliance* own the hard temperature control (a
  sealed cooker or induction unit with its own thermostat holds the target), and use
  a cheap IR sensor as an independent safety/monitoring check rather than the primary
  controller. That keeps your robot out of the business of precise thermal control.

## Compute and storage

- **Training/inference workstation.** Imitation learning wants a GPU. A desktop with
  a modern mid-to-high NVIDIA card (something in the RTX 40-series range) is the
  typical setup; a used card is a fine way to cut cost. Rough budget $1,000–2,500
  for a capable machine, less if you already have a GPU.
- **Datasets.** Teleop recordings (synchronized video + joint data) get large fast —
  many gigabytes per session once you have hundreds of demos. Your ~400 GB of free
  Google Drive space is a sensible place to archive and version those datasets and
  trained checkpoints, so the local workstation disk stays for active work. Plan the
  data pipeline around that from day one rather than filling a local drive.
- A small **microcontroller** (Arduino/ESP32-class, ~$10–30) hosts the IR temp sensor
  and any safety interlocks, reporting to the main machine.

## Recommended v1 bill of materials (Path A)

| Item | Approx. cost |
|---|---|
| SO-101 arm (kit or DIY) | $300–500 |
| Intel RealSense D435 (overhead depth) | $300–350 |
| USB wrist webcam | $20–60 |
| Printed/lined gripper fingertips (spoon + cup) | ~$20 materials |
| MLX90614 IR temp sensor + microcontroller | $30–60 |
| Sealed cooking appliance (induction + covered vessel or multicooker) | $80–250 |
| Ingredient dispensers / fixed stations (largely 3D-printed) | $50–150 |
| Even LED work lighting | $30–60 |
| Frame / mounting (extrusion, board) | $50–150 |
| Compute (if a GPU box is needed) | $0–2,500 |
| **Total (excluding compute)** | **~$900–1,600** |
| **Total (with a new GPU workstation)** | **~$1,900–4,100** |

Compare to Path B, where the arm alone is ~$8,800 and the safety and mounting work
multiplies from there.

## Student / minimal-budget build

The ~$900–1,600 figure above buys convenience. Almost none of it is actually
required, and the two biggest line items (the depth camera and a GPU workstation)
can go to roughly zero. Here is the honest floor.

**The arm is mostly 3D-printed, so you build it, not buy it.** The SO-101 is an
open-source design: the structural parts are printed and the only real cost is the
servos. It uses Feetech STS3215 smart servos at roughly $13–15 each. A single
6-servo follower arm is therefore about **$80–110 in motors**, plus a small servo
driver board (~$10) and printed parts. If your university has a makerspace or
robotics lab, the printing is free or nearly so — go find it, this is the single
biggest cost lever you have. Do **not** cheap out to dumb hobby servos: the STS3215
gives position feedback, which imitation learning needs. That is the one part worth
its price.

- **Teleoperation the cheap way.** The standard SO-101 setup is a leader + follower
  pair (two arms, ~$200 in servos total) so you move the leader by hand and the
  follower records the demo. If even that is too much, build only the follower and
  collect demos by *kinesthetic teaching* — physically backdriving the arm by hand —
  or by keyboard/phone control. Lower-quality data, but $0 extra hardware.

**Skip the depth camera.** This is where I over-specced. Mainstream LeRobot
imitation learning (ACT and similar) runs on **ordinary RGB USB webcams** — depth is
a nice-to-have, not a requirement. Two cheap webcams (one overhead, one at the
wrist) at ~$15–25 each cover it. That deletes ~$300 of RealSense in one move.

**Compute is free.** You do not need a GPU box. LeRobot policies train on **Google
Colab's free GPU tier** (or Kaggle's free GPUs), and many universities give students
GPU cluster access — ask. Budget $0, or ~$10/month for Colab Pro if free-tier limits
bite. Your ~400 GB of Google Drive holds the datasets and checkpoints and mounts
straight into Colab.

**Make v1 cold — no appliance, no heat.** For the first build, pick a dish that
needs *zero cooking*: overnight oats, an assembled grain/yogurt bowl, a smoothie
prep. This deletes the appliance cost, deletes the temperature sensor, and deletes
almost the entire safety burden at once. The robot moves a spoon and light
pre-portioned cups. Heat is a v2 problem.

**Scrounge the rest.** Stations, dispensers, and the frame can be printed,
repurposed food containers, or a plywood board. Call it $0–20.

### Minimal student bill of materials

| Item | Approx. cost |
|---|---|
| Follower-arm servos (6× STS3215) | $80–110 |
| Servo driver board + cables | $10–20 |
| 3D-printed arm parts | $0 (makerspace) – $30 |
| Second arm for teleop (leader), optional | +$90–110 |
| Two USB webcams (overhead + wrist) | $30–50 |
| Compute (Colab free / university cluster) | $0 |
| Appliance / temp sensor (cold-assembly v1) | $0 |
| Frame + stations (printed / scrounged) | $0–20 |
| **Total, single-arm kinesthetic** | **~$120–230** |
| **Total, leader + follower pair** | **~$210–340** |

That is a realistic student number, and the single most important move is checking
whether your school's robotics lab or makerspace already has arms, printers, and GPU
access you can use for free — that can take the hardware cost close to the price of a
couple of webcams.

## What this means for starting the design

1. **Commit to Path A for v1.** Freeze a rule into the design: nothing over ~400 g
   moves under robot power. That single rule sizes your arm, your budget, and your
   safety envelope all at once.
2. **Pick the arm (SO-101) and order it**, because the physical rig gates everything
   downstream and the arm has the longest "learn to use it" tail.
3. **Design the cell around a fixed appliance and a fixed pot.** The robot's job is
   spoon + light ingredients; the appliance's job is heat and the heavy vessel.
4. **Buy the overhead RealSense and a wrist webcam** — the perception pair the
   software stack expects.
5. **Treat temperature as monitoring, not control, in v1** — appliance holds the
   temp, cheap IR sensor watches it.

Once the arm and cameras are in hand, the next design artifact is the physical cell
layout: where the appliance, the spoon rest, the ingredient stations, and the two
cameras sit, in fixed known positions, so the manipulation problem stays bounded.
