---
name: icon-craft
description: House rules for designing and shipping icons — metaphor originality, grid and stroke system, SVG hygiene, and the real-size proof step. Use whenever authoring, reviewing, or exporting an icon or icon set.
---

# Icon craft

## 1. Metaphor before geometry

An icon fails at the concept stage, not the bezier stage. Before drawing:

- Name the **behaviour** the icon stands for, not the object.
- Write 3 structurally different metaphors. Three drawings of a bell are one metaphor.
- Say the cliché out loud and reject it (gear, magnifier, bell, lightbulb, rocket, sparkles). Use it only when it is genuinely the clearest signal — and then differentiate in execution.
- Kill any metaphor that is culture-bound, depends on text, or needs more than 2 elements to read.
- Read the metaphor ledger and treat every metaphor in it as spent. Two icons must never resolve to the same silhouette.

Test each candidate against: *would a stranger name this in under a second, with no label, at the target size, in one colour?*

## 2. House system (defaults — name them if you deviate)

| Property | Default |
| --- | --- |
| Canvas | 16×16, no padding (keyline runs to the edge) |
| Keyline | Square |
| Style | Outline, 2D flat |
| Stroke | 1px, `round` cap, `round` join |
| Corner radius | ~2px |
| Colour | Single colour, `currentColor` — no gradients, no shadows |
| Density | Same number of visual elements across a set (±1) |

Larger targets scale the system, they don't change it: at 24px keep 1.5px stroke, at 32px keep 2px — optical weight stays constant.

Alignment rules that matter more than the grid:

- Draw strokes on **half-pixel centres** (`x.5`) so a 1px stroke lands on a whole pixel and stays crisp.
- Optically centre, don't mathematically centre. Triangles and diagonals need shifting; circles need to overshoot the square's bounds slightly to look the same size.
- Horizontal and vertical edges beat diagonals; 45° beats an arbitrary angle.

## 3. SVG hygiene (non-negotiable)

- Hand-author `d` attributes with coordinates you can explain. No traced decimal soup.
- `viewBox="0 0 16 16"`, no `width`/`height` when the icon will be sized by CSS.
- Use `fill="none" stroke="currentColor"` on the root and let paths inherit.
- **Never** put CSS custom properties (`var(...)`) in an SVG `<style>` block — they silently fail when the file is loaded via `<img>` or as a favicon. Put values on the path attributes.
- **Never** write a double hyphen inside an XML comment. It makes the whole file invalid XML and it renders as *nothing*, silently.
- Validate every file before shipping:

  ```bash
  xmllint --noout <dir>/*.svg
  ```

## 4. Prove it at real size

Claiming an icon works without looking at its pixels is not allowed. Icon Studio ships the renderer:

```bash
python3 ~/.kiro/crew/apps/icon-studio/scripts/contact_sheet.py --job <job-id>
```

It builds a contact sheet of every SVG in the job at each target size (plus a 2x row) on light and dark bands, rasterizes it with headless Chrome, writes a PNG, and prints the path.

Then **read the PNG and judge it**. Look for:

- strokes that smear instead of landing on a pixel
- counters (enclosed gaps) that fill in at the smallest size
- two icons with the same silhouette
- one icon visually heavier or busier than its neighbours

Fix, re-render, and only then present it. Embed it with `![contact sheet](/absolute/path.png)`.

If the renderer reports no browser, say so plainly instead of shipping unverified icons — the fix is `npx playwright install chromium` or any Chrome/Chromium on `PATH`.

## 5. Set review checklist

Before handing off a set, every item must pass:

- [ ] Each icon nameable in under a second with no label
- [ ] No two icons share a silhouette
- [ ] Identical stroke weight, cap, join, corner radius across the set
- [ ] Same visual density — no icon carries twice the detail of its neighbours
- [ ] Legible at the smallest target size, verified on the contact sheet
- [ ] Works in one colour, on light and dark
- [ ] Valid XML, `currentColor`, no stray `width`/`height`, no `<style>` block
- [ ] Metaphor ledger updated with what was used and what was rejected
