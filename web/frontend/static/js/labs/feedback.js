/**
 * A lab's review, as text to paste back into a conversation (D-082).
 *
 * Written for a reader who has not seen the screen: the decision on the round, every choice by
 * the label the user saw (marked when it was left as the lab started it), the verdict on each
 * layout, each note beside what it is about, the combinations worth keeping, and the raw settings
 * at the end so the exact arrangement can be rebuilt.
 */

const VERDICTS = { love: "Love it", ok: "It's OK", no: "Not this one" };
export const DECISIONS = {
  approve: "Approved — build it",
  changes: "Approved with the changes below",
  no: "Not yet — another round",
};

function settingsLines(review, settings, { onlyChanged = false } = {}) {
  const { sections, defaults } = review.spec;
  const lines = [];
  for (const section of sections) {
    for (const option of section.options) {
      const value = settings[option.id];
      const changed = JSON.stringify(value) !== JSON.stringify(defaults[option.id]);
      if (onlyChanged && !changed) continue;
      // Left as the lab started it — which, from the second round on, is the previous round's
      // choice — is not the same as choosing it again.
      const mark = !onlyChanged && !changed ? " (unchanged)" : "";
      lines.push(`- ${section.label} → ${option.label}: ${review.describeValue(option.id, value)}${mark}`);
    }
  }
  return lines;
}

export function feedbackMarkdown(review, { count = null } = {}) {
  const { lab, spec } = review;
  const out = [`## ${spec.title}`, ""];
  const where = count === null ? "" : ` — ${count} recordings on screen`;
  out.push(`From ${spec.page} on ${new Date().toLocaleString()}${where}.`);

  if (lab.decision) out.push("", `**Decision:** ${DECISIONS[lab.decision]}`);

  out.push("", "### What I picked");
  out.push(...settingsLines(review, lab.settings));

  const layouts = spec.layouts.filter((layout) => lab.verdicts[layout.id]?.score || lab.verdicts[layout.id]?.note);
  if (layouts.length) {
    out.push("", "### Layouts");
    for (const layout of layouts) {
      const { score = "", note = "" } = lab.verdicts[layout.id];
      const verdict = VERDICTS[score] ?? "No verdict";
      out.push(`- **${layout.label}** — ${verdict}${note.trim() ? `: ${note.trim()}` : ""}`);
    }
  }

  const notes = spec.sections.filter((section) => lab.notes[section.id]?.trim());
  if (notes.length) {
    out.push("", "### Notes on each part");
    for (const section of notes) out.push(`- **${section.label}:** ${lab.notes[section.id].trim()}`);
  }

  if (lab.combos.length) {
    out.push("", "### Combinations I saved");
    lab.combos.forEach((combo, index) => {
      out.push("", `${index + 1}. **${combo.name}**${combo.note?.trim() ? ` — ${combo.note.trim()}` : ""}`);
      const changed = settingsLines(review, combo.settings, { onlyChanged: true });
      out.push(...(changed.length ? changed : ["- (all defaults)"]).map((line) => `   ${line}`));
    });
  }

  if (lab.general?.trim()) out.push("", "### Overall", lab.general.trim());

  out.push("", "<details><summary>Exact settings</summary>", "", "```json");
  out.push(JSON.stringify({ settings: lab.settings, combos: lab.combos.map((c) => ({ name: c.name, settings: c.settings })) }));
  out.push("```", "</details>");
  return out.join("\n");
}
