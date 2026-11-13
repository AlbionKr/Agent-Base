# Design notes

Notes from three videos on making AI-built interfaces look deliberate, and the rules this project follows because of them. The rules are enforced by `frontend/src/design.test.ts`; `npm test` fails if a change breaks one.

## What the videos say

### 1. "How to Make AI-Generated UI Look Expensive (7 Fixes)" (Griffin Wooldridge)
<https://www.youtube.com/watch?v=eQ74n4huAcA>

Every good screen gets seven things right:

1. **One focal point.** Decide what the screen is for and make that the biggest, highest-contrast thing; turn everything else down. Usually this means removing, not adding.
2. **Spacing on a scale.** Multiples of 4 or 8 px. Spacing shows grouping: a label sits close to its value, items in a section are closer than the sections are to each other.
3. **Hierarchy from weight and colour, not size.** One or two fonts, a heading only slightly bigger but heavier and darker, secondary text in a softer grey, slightly tighter letter spacing on headings, short lines.
4. **Little colour.** Mostly neutrals, tinted toward the brand. One accent, used only for the main button, focus and links.
5. **Real content.** Real names, numbers and copy written for this product. No placeholders.
6. **Details.** One corner radius family, nested elements slightly less rounded than their container, 1 px subtle borders, soft layered shadows, icons the same size and stroke.
7. **Motion tied to the action.** A panel grows out of what was clicked, opens a little slower than it closes, notifications slide in from an edge and stack, every animation short with an ease-out.

### 2. "How to Make Your AI-Built Product Look Worth Paying More For" (Tim Gabe)
<https://www.youtube.com/watch?v=pqWPKgfBDiw>

- People judge care by the look. In high-trust areas (health, **finance**, law) a look that came straight out of a prompt makes buyers wonder where else corners were cut.
- **Decide what "premium" means to your buyer before designing.** "Make it premium" leaves the AI guessing; calm and warm, or sharp and fast, is your call.
- "Fine" is the floor now: most AI-built pages land in a handful of looks. Known tells: one font for everything (Inter), purple-to-blue gradients, generic components.
- Give the AI real references and a designer's written rules, then add **one thing only you would make** (your own icons, type, or one idea that belongs to you).
- The website should be built from the same parts as the product.

### 3. "Creator Of Taste Skill Just Revealed His Entire AI Design Workflow" (AI LABS, on Leon Lynn's method)
<https://www.youtube.com/watch?v=-bv2xKQzZio>

1. Short prompt, strong references (9 to 12 images) instead of long descriptions.
2. Make a target first: quick mockups, several variants side by side, pick one.
3. Set the bar as a number, and correct the agent when it grades itself too high.
4. Give it time; it otherwise stops as soon as the page works.
5. Ban the slop: a written list of patterns that are not allowed. A ban list only stops what it names.
6. Build the static design first, get it right, then add motion.
7. Give specific feedback: which element, what to change, by how much.
8. Let a different model, with no knowledge of how it was built, judge the work against a score it must reach.

## What this means for Agent Base

**Who it is for, and what premium means to them.** Someone handing money to an AI agent. For them premium means *calm and exact*: nothing shouts, every number is precise and explained, colour appears only when something happened. Not a game, not a crypto casino.

**The one thing only this product has.** The vault as a glowing core with the agents in orbit around it, and every movement on screen tied to a real event in the audit log. Everything else stays quiet so that stays the memorable thing.

**Focal point per screen.**
- Base view: what got taken *with the guard*, against what got taken without it. The "with the guard" figure is the largest thing on screen; the other two figures are half its size; the verdict's first line is grey and its point is white.
- Dashboard: the five numbers across the top, then the red-team verdict sentence.
- Detail panel: the node's name and its status tag.

## The rules (enforced by `design.test.ts`)

| Rule | Why | Check |
|---|---|---|
| Font sizes only from the type scale 11 / 12 / 13 / 14 / 16 / 20 / 24 / 32 / 40, plus the two figure tokens | few sizes, hierarchy from weight and colour (video 1, fix 3) | any other px size in `font` / `font-size` fails |
| Spacing only on the 4 px scale (1 to 2 px allowed for hairlines) | video 1, fix 2 | any other padding, margin or gap fails |
| Radius from tokens: 12 containers, 8 things inside them, 4 chips; pills and circles | nested is smaller than its container (fix 6) | any other px radius fails |
| No uppercase, no tracked-out letter spacing | the commonest "AI template" tell (video 2) | `text-transform: uppercase` or positive `letter-spacing` fails |
| Three weights: 400, 500, 600 | weight carries hierarchy, so it must be consistent | any other weight fails |
| Monospace only for machine data: addresses, hashes, audit event ids, times, news item ids | mono for ordinary labels is a tell | `var(--mono)` outside the allowlist fails |
| Colours only from the tokens in `:root`; tints via `color-mix()` of a token | one accent, one colour per outcome, neutrals from one family (fix 4) | a hex or tinted `rgba()` outside `:root` fails |
| No gradient text, no linear-gradient washes | purple-to-blue washes are a tell (video 2) | `background-clip` or `linear-gradient` fails |
| Interface transitions 300 ms or less | short, ease-out (fix 7) | a longer `transition` fails |
| Fonts only through `--ui` (Geist) and `--mono` (Geist Mono) | one or two families (fix 3) | any other `font-family` fails |
| No placeholder copy, no `→` on buttons, no `A · B · C` meta strings | real content (fix 5); template chrome | the strings fail the copy check |

Colour roles, unchanged: amber `--accent` means money and is also the main button and focus ring; green allowed, red attack or loss, violet a person decides, ice blue frozen. Links are neutral (`--text-2`), because ice blue already means "frozen".

Motion: the detail panel grows out of the node it belongs to on a spring and closes in 180 ms; notices slide up from the bottom of the scene and stack (two at most); the stepper's progress line moves in 300 ms. With `prefers-reduced-motion` none of this moves.

## How to change the interface from here

1. Say which screen and which element, and what it is for.
2. Change the static design first; screenshot it at 1440 by 900 with `?step=4` and `?step=7`.
3. Add or change motion only after the static screenshot is right.
4. Run `npm test`. A design rule failing means the change uses a value off the scales: use a token, or change the rule here first, with a reason.
5. Have a reviewer that did not make the change (another model, fresh context) score before and after on the seven questions of video 1 plus the tells of video 2. The bar is 8.5 out of 10.

## Not applied, and why

- **9 to 12 reference images and three mockup variants** (video 3, steps 1 and 2). The layout was already set by an earlier brief and the deadline is close; this pass refined an existing design rather than starting a new one.
- **A 99.9% bar and multi-hour runs** (video 3, steps 3 and 4). The bar used here is the reviewer's 8.5 out of 10.
- **A marketing website** (video 2). The project has no website; the submission README plays that role.
