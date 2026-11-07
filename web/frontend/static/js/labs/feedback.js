/**
 * The review, as text to paste back into a conversation (D-080).
 *
 * Written for a reader who has not seen the screen: every choice by the label the user saw, the
 * verdict on each layout, each note beside what it is about, the combinations worth keeping, and
 * the raw settings at the end so the exact arrangement can be rebuilt.
 */

import { DEFAULTS, LAYOUTS, SECTIONS, describeValue } from "./options.js";

const VERDICTS = { love: "Love it", ok: "It's OK", no: "Not this one" };

function settingsLines(settings, { onlyChanged = false } = {}) {
  const lines = [];
  for (const section of SECTIONS) {
    for (const option of section.options) {
      const value = settings[option.id];
      const changed = JSON.stringify(value) !== JSON.stringify(DEFAULTS[option.id]);
      if (onlyChanged && !changed) continue;
      // "(default)" says the user left it alone, which is not the same as choosing it.
      const mark = !onlyChanged && !changed ? " (default)" : "";
      lines.push(`- ${section.label} → ${option.label}: ${describeValue(option.id, value)}${mark}`);
    }
  }
  return lines;
}

export function feedbackMarkdown(lab, { count = 0 } = {}) {
  const out = [];
  out.push("## Recordings lab feedback");
  out.push("");
  out.push(
    `From /labs/sessions/ on ${new Date().toLocaleString()} — ${count} recordings on screen.`,
  );

  out.push("", "### What I picked");
  out.push(...settingsLines(lab.settings));

  const verdicts = LAYOUTS.filter((layout) => lab.verdicts[layout.id]?.score || lab.verdicts[layout.id]?.note);
  if (verdicts.length) {
    out.push("", "### Layouts");
    for (const layout of verdicts) {
      const { score = "", note = "" } = lab.verdicts[layout.id];
      const verdict = VERDICTS[score] ?? "No verdict";
      out.push(`- **${layout.label}** — ${verdict}${note.trim() ? `: ${note.trim()}` : ""}`);
    }
  }

  const notes = SECTIONS.filter((section) => lab.notes[section.id]?.trim());
  if (notes.length) {
    out.push("", "### Notes on each part");
    for (const section of notes) out.push(`- **${section.label}:** ${lab.notes[section.id].trim()}`);
  }

  if (lab.combos.length) {
    out.push("", "### Combinations I saved");
    lab.combos.forEach((combo, index) => {
      out.push("", `${index + 1}. **${combo.name}**${combo.note?.trim() ? ` — ${combo.note.trim()}` : ""}`);
      const changed = settingsLines(combo.settings, { onlyChanged: true });
      out.push(...(changed.length ? changed : ["- (all defaults)"]).map((line) => `   ${line}`));
    });
  }

  if (lab.general?.trim()) out.push("", "### Overall", lab.general.trim());

  out.push("", "<details><summary>Exact settings</summary>", "", "```json");
  out.push(JSON.stringify({ settings: lab.settings, combos: lab.combos.map((c) => ({ name: c.name, settings: c.settings })) }));
  out.push("```", "</details>");
  return out.join("\n");
}
