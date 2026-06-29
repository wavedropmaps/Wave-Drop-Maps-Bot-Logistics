---
name: frontend-design
description: >
  UI/UX specialist for generating production-grade, visually distinctive frontend code (React, Vue, Svelte, HTML/CSS). Use this skill whenever the user wants to build a UI, component, page, landing page, dashboard, or any visual interface. Automatically triggers on requests to design, style, or build anything visual — even if the user doesn't say "design". Stops generic AI aesthetics (purple gradients, Inter font, scattered micro-interactions) by forcing aesthetic commitment before any code is written.
---

# Frontend Design

A design framework that forces aesthetic commitment before writing a single line of code. No generic AI slop.

---

## Step 1 — Answer the four questions before touching code

Before any implementation, establish:

1. **Purpose** — what is this UI actually for? Who uses it and in what context?
2. **Tone** — what aesthetic direction? Pick one and commit: brutalist, maximalist, retro-futuristic, luxury, playful, editorial, corporate, cyberpunk, organic, minimalist, etc.
3. **Constraints** — technical limits (framework, existing design system, brand colours, accessibility requirements)?
4. **Differentiation** — what makes this look like it was made by a human with taste, not an AI on autopilot?

Present the answers to the user and get confirmation before proceeding. Do not skip this step for "simple" components — simple components are where generic defaults sneak in.

---

## Step 2 — Commit to the five design dimensions

For every UI, make explicit, opinionated choices across all five:

### Typography
- Pick distinctive display + body font pairings
- **Forbidden defaults:** Inter, Roboto, Arial, system-ui as the sole font
- Use aggressive size hierarchy — 3× or more difference between heading levels
- Consider: editorial serif + grotesque sans, geometric display + humanist body, monospace display for technical aesthetics

### Colour & Theme
- Define CSS custom properties upfront: dominant colour, sharp accent, background, surface, text
- The palette should match the aesthetic — brutalist uses raw black/white + one scream colour; luxury uses near-blacks and warm metallics; retro uses desaturated brights with grain
- Avoid: generic blue primary + grey neutrals unless the constraint demands it

### Motion
- One coordinated motion moment per page — a page-load reveal sequence, a hero entrance, a transition theme
- Not scattered micro-interactions on every element
- Motion should feel intentional, not sprinkled on as an afterthought
- Prefer: staggered reveals, morphing shapes, scroll-linked transformations

### Spatial Composition
- Break the grid intentionally — asymmetry, overlapping elements, elements that bleed off-screen
- Use extreme whitespace or extreme density depending on aesthetic (not the safe middle)
- Avoid: equal padding everywhere, every section the same height, centred everything

### Backgrounds
- Never use flat white or flat grey as background unless minimalism is the explicit aesthetic
- Options: gradient meshes, subtle noise/grain textures, layered depth with multiple z-levels, bold colour fills, photography with blend modes

---

## Step 3 — Implementation principles

- Write real CSS — not just Tailwind utility soup that produces the same layout every time
- CSS custom properties for the design system, not hardcoded values
- Semantic HTML — the structure should make sense without the styles
- Complexity should match the aesthetic: maximalist needs elaborate execution, minimalist demands restraint and precision
- If using a component library (shadcn, MUI, etc.), override its defaults aggressively — don't accept the library's aesthetic as your own

---

## Example: weak vs strong prompt

**Weak:** "Make a landing page with a hero, features section, and CTA"
→ Produces: Inter font, purple gradient, floating cards with shadows, generic layout

**Strong:** "Make a landing page — editorial aesthetic, newspaper-inspired typography, high contrast black/white with one blood-orange accent, bold grid-breaking layout, no gradients"
→ Produces: something with a point of view

If the user gives a weak prompt, ask the four questions before generating. If they give a strong prompt, confirm the aesthetic direction and proceed.

---

## Aesthetic reference points

| Aesthetic | Typography | Colour | Motion | Layout |
|---|---|---|---|---|
| Brutalist | Heavy grotesque, raw sizing | Black/white + scream accent | Abrupt, no easing | Broken grid, collision |
| Luxury | Refined serif display | Near-black, warm metallics, cream | Slow, precise reveals | Generous whitespace, restraint |
| Retro-futuristic | Geometric sans, monospace | Desaturated brights, CRT green | Scanlines, glitch | Grid-locked, technical |
| Editorial | High-contrast serif | Limited palette, paper tones | Scroll-driven, cinematic | Asymmetric, magazine-style |
| Maximalist | Mixed typefaces, size chaos | Everything, layered | Constant, orchestrated | Dense, overlapping |
| Playful | Rounded, variable fonts | Saturated, unexpected combos | Bouncy, spring physics | Irregular, surprising |
