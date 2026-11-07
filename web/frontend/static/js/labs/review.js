/**
 * One lab's review in progress (D-080): its settings, a verdict and a note per layout, a note per
 * section of options, saved combinations, a decision on the round, and a general note.
 *
 * Shared by every lab page so each one is customised, judged and exported the same way. Kept in
 * this browser's local storage, which is a convenience: a private window or cleared site data
 * starts a fresh review rather than failing.
 *
 * `spec`: `{ storageKey, sections, defaults, layouts, title, page }`. A section is
 * `{ id, label, options }`; an option is `{ id, kind: "choice"|"multi"|"toggle", label, choices }`.
 */

export function createReview(spec) {
  const options = Object.fromEntries(
    spec.sections.flatMap((section) => section.options.map((option) => [option.id, { ...option, section }])),
  );

  function sanitize(stored = {}) {
    const settings = { ...spec.defaults };
    for (const [id, value] of Object.entries(stored ?? {})) {
      const option = options[id];
      if (!option) continue;
      if (option.kind === "toggle" && typeof value === "boolean") settings[id] = value;
      if (option.kind === "choice" && option.choices.some((c) => c.id === value)) settings[id] = value;
      if (option.kind === "multi" && Array.isArray(value)) {
        settings[id] = value.filter((v) => option.choices.some((c) => c.id === v));
      }
    }
    return settings;
  }

  /** "Keywords from the transcript" for `title=keywords`; joined labels for a multi. */
  function describeValue(id, value) {
    const option = options[id];
    if (!option) return String(value);
    if (option.kind === "toggle") return value ? "On" : "Off";
    const label = (choice) => option.choices.find((c) => c.id === choice)?.label ?? choice;
    if (option.kind === "multi") return value.length ? value.map(label).join(", ") : "None";
    return label(value);
  }

  const fresh = () => ({ settings: sanitize(), verdicts: {}, notes: {}, combos: [], general: "", decision: "" });

  function read() {
    try {
      const raw = window.localStorage.getItem(spec.storageKey);
      if (!raw) return fresh();
      const stored = JSON.parse(raw);
      return {
        ...fresh(),
        ...stored,
        settings: sanitize(stored.settings),
        combos: Array.isArray(stored.combos) ? stored.combos : [],
      };
    } catch {
      return fresh();
    }
  }

  const lab = read();
  const listeners = new Set();

  function save(changed = []) {
    try {
      window.localStorage.setItem(spec.storageKey, JSON.stringify(lab));
    } catch {
      // Storage is a convenience here; the page works without it.
    }
    for (const listener of listeners) listener(changed);
  }

  return {
    spec,
    lab,
    sanitize,
    describeValue,
    /** Call `listener(changedKeys)` after every change. */
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    setSetting(id, value) {
      lab.settings[id] = value;
      save([id]);
    },
    applySettings(settings) {
      lab.settings = sanitize(settings);
      save(Object.keys(lab.settings));
    },
    /** `score`: "love", "ok" or "no"; the same score again clears it. */
    setVerdict(layoutId, score) {
      const current = lab.verdicts[layoutId] ?? {};
      lab.verdicts[layoutId] = { ...current, score: current.score === score ? "" : score };
      save(["verdicts"]);
    },
    setVerdictNote(layoutId, note) {
      lab.verdicts[layoutId] = { ...(lab.verdicts[layoutId] ?? {}), note };
      save(["verdictNote"]);
    },
    setNote(sectionId, note) {
      lab.notes[sectionId] = note;
      save(["notes"]);
    },
    setGeneral(note) {
      lab.general = note;
      save(["general"]);
    },
    /** The round as a whole: "approve", "changes" or "no"; the same again clears it. */
    setDecision(decision) {
      lab.decision = lab.decision === decision ? "" : decision;
      save(["decision"]);
    },
    saveCombo(name, note = "") {
      lab.combos.push({
        name: name || `Combination ${lab.combos.length + 1}`,
        note,
        settings: { ...lab.settings },
        savedAt: new Date().toISOString(),
      });
      save(["combos"]);
    },
    removeCombo(index) {
      lab.combos.splice(index, 1);
      save(["combos"]);
    },
    resetReview() {
      Object.assign(lab, fresh());
      save(Object.keys(lab.settings).concat(["verdicts", "notes", "combos", "general", "decision"]));
    },
  };
}
