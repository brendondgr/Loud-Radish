/* Which panel is where — the part of the exported application the user rearranges.
 *
 * Three panels, two columns, and four operations: collapse, move, close, reopen. Everything the
 * arrangement can do is one of those, and the "video only" case is not a mode with its own code
 * path — it is what falls out of closing the assistant, because a column with nothing in it takes
 * no width and the transcript moves across to fill the space that opened.
 *
 * The arrangement is remembered per recording. Someone who set a talk up the way they like it and
 * came back to it the next day should find it as they left it.
 */

const DEFAULT_LAYOUT = {
  left: ["video", "transcript"],
  right: ["qa"],
  collapsed: [],
  closed: [],
  leftWidth: "58%",
};

const PANEL_LABELS = { video: "Video", transcript: "Transcript", qa: "Assistant" };

/** The split once the assistant is closed. The point of closing it is to see more of the talk. */
const VIDEO_FIRST_WIDTH = "72%";

class Layout {
  constructor() {
    this.saved = scopedStore("layout");
    this.state = { ...DEFAULT_LAYOUT, ...this.saved.read({}) };

    // Panels are cloned out of a `<template>` rather than written into a column in the markup, so
    // the saved arrangement is the only thing that decides where they start. Markup and saved
    // state disagreeing on first paint is a flash of the wrong layout on every load.
    this.panels = new Map();
    for (const node of $$("[data-panel]", $("[data-panels]").content)) {
      this.panels.set(node.dataset.panel, node.cloneNode(true));
    }

    this.columns = { left: $('[data-column="left"]'), right: $('[data-column="right"]') };
    this.reopenBar = $("[data-reopen]");

    this._wirePanels();
    this._wireSplitter();
    $("[data-reset-layout]").addEventListener("click", () => this.reset());
    this.apply();
  }

  panel(name) {
    return this.panels.get(name);
  }

  /** Render the state. Every other method changes it and calls this. */
  apply() {
    for (const [side, column] of Object.entries(this.columns)) {
      column.replaceChildren(
        ...this._visible(side)
          .map((name) => this.panels.get(name))
          .filter(Boolean)
      );
    }
    for (const [name, node] of this.panels) {
      const collapsed = this.state.collapsed.includes(name);
      node.dataset.collapsed = String(collapsed);
      const button = $("[data-panel-collapse]", node);
      if (button) {
        button.textContent = collapsed ? "▸" : "▾";
        button.title = collapsed ? "Expand" : "Collapse";
      }
    }
    document.documentElement.style.setProperty("--left-width", this.state.leftWidth);
    this._renderReopen();
    this.saved.write(this.state);
  }

  // -- the four operations ---------------------------------------------------------

  move(name) {
    const from = this.state.left.includes(name) ? "left" : "right";
    const to = from === "left" ? "right" : "left";
    this.state[from] = this.state[from].filter((entry) => entry !== name);
    this.state[to] = [...this.state[to], name];
    this.apply();
  }

  toggleCollapse(name) {
    this.state.collapsed = this.state.collapsed.includes(name)
      ? this.state.collapsed.filter((entry) => entry !== name)
      : [...this.state.collapsed, name];
    this.apply();
  }

  close(name) {
    if (!this.state.closed.includes(name)) this.state.closed = [...this.state.closed, name];

    // Closing the assistant empties the right column. Rather than leave the video and the
    // transcript stacked in a half-width column with nothing beside them, the transcript moves
    // across and the split widens — which is the "video first" arrangement, reached by applying
    // the ordinary rules rather than by a mode with its own code path.
    if (name === "qa" && !this._visible("right").length && this.state.left.includes("transcript")) {
      this.state.left = this.state.left.filter((entry) => entry !== "transcript");
      this.state.right = [...this.state.right, "transcript"];
      // Remembered rather than reset on reopen, so a user who had dragged the splitter somewhere
      // deliberate gets that back instead of the default.
      this.state.priorWidth = this.state.leftWidth;
      this.state.leftWidth = VIDEO_FIRST_WIDTH;
    }
    this.apply();
  }

  open(name) {
    this.state.closed = this.state.closed.filter((entry) => entry !== name);
    // Reopening the assistant takes its column back, so the transcript returns under the video
    // rather than being squeezed beside it.
    if (name === "qa" && this.state.right.includes("transcript")) {
      this.state.right = this.state.right.filter((entry) => entry !== "transcript");
      this.state.left = [
        ...this.state.left.filter((entry) => entry !== "transcript"),
        "transcript",
      ];
      this.state.leftWidth = this.state.priorWidth ?? DEFAULT_LAYOUT.leftWidth;
      delete this.state.priorWidth;
    }
    if (!this.state.left.includes(name) && !this.state.right.includes(name)) {
      this.state.right = [...this.state.right, name];
    }
    this.apply();
  }

  reset() {
    this.state = { ...DEFAULT_LAYOUT };
    this.apply();
  }

  /** Put `name` into `side`, ordered by where in the column it was dropped. */
  placeAt(name, side, clientY) {
    const other = side === "left" ? "right" : "left";
    this.state[other] = this.state[other].filter((entry) => entry !== name);
    const rest = this.state[side].filter((entry) => entry !== name);

    let index = rest.length;
    for (const [position, entry] of rest.entries()) {
      const node = this.panels.get(entry);
      if (!node?.isConnected) continue;
      const box = node.getBoundingClientRect();
      if (clientY < box.top + box.height / 2) {
        index = position;
        break;
      }
    }
    rest.splice(index, 0, name);
    this.state[side] = rest;
    this.state.closed = this.state.closed.filter((entry) => entry !== name);
    this.apply();
  }

  // -- internals -------------------------------------------------------------------

  _visible(side) {
    return (this.state[side] ?? []).filter((name) => !this.state.closed.includes(name));
  }

  _renderReopen() {
    // Only closed panels get a button. Three permanently lit toggles would be chrome; a bar that
    // is empty until something is missing means something when it is not.
    this.reopenBar.replaceChildren(
      ...this.state.closed.map((name) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "button";
        button.textContent = `Reopen ${PANEL_LABELS[name] ?? name}`;
        button.addEventListener("click", () => this.open(name));
        return button;
      })
    );
  }

  _wirePanels() {
    for (const [name, node] of this.panels) {
      $("[data-panel-close]", node)?.addEventListener("click", () => this.close(name));
      $("[data-panel-collapse]", node)?.addEventListener("click", () => this.toggleCollapse(name));
      $("[data-panel-move]", node)?.addEventListener("click", () => this.move(name));

      // Dragging by the header, with the move button as its keyboard-reachable equivalent. Both,
      // because a layout only a mouse can change is a layout half the users cannot.
      const head = $(".panel__head", node);
      head.draggable = true;
      head.addEventListener("dragstart", (event) => {
        event.dataTransfer.setData("text/plain", name);
        event.dataTransfer.effectAllowed = "move";
        node.dataset.dragging = "true";
      });
      head.addEventListener("dragend", () => {
        node.dataset.dragging = "false";
      });
    }

    for (const [side, column] of Object.entries(this.columns)) {
      column.addEventListener("dragover", (event) => event.preventDefault());
      column.addEventListener("drop", (event) => {
        event.preventDefault();
        const name = event.dataTransfer.getData("text/plain");
        if (this.panels.has(name)) this.placeAt(name, side, event.clientY);
      });
    }
  }

  _wireSplitter() {
    const splitter = $("[data-splitter]");
    const workspace = $("[data-workspace]");
    let dragging = false;

    const setFromX = (clientX) => {
      const box = workspace.getBoundingClientRect();
      const ratio = ((clientX - box.left) / box.width) * 100;
      this.state.leftWidth = `${Math.min(85, Math.max(15, ratio)).toFixed(1)}%`;
      // Written straight to the custom property while dragging: going through `apply` would
      // rebuild both columns on every pointer move, which drops the video's frames.
      document.documentElement.style.setProperty("--left-width", this.state.leftWidth);
    };

    splitter.addEventListener("pointerdown", (event) => {
      dragging = true;
      splitter.setPointerCapture(event.pointerId);
    });
    splitter.addEventListener("pointermove", (event) => {
      if (dragging) setFromX(event.clientX);
    });
    splitter.addEventListener("pointerup", () => {
      dragging = false;
      this.saved.write(this.state);
    });
    splitter.addEventListener("keydown", (event) => {
      const step = event.key === "ArrowLeft" ? -5 : event.key === "ArrowRight" ? 5 : 0;
      if (!step) return;
      event.preventDefault();
      const current = Number.parseFloat(this.state.leftWidth) || 58;
      this.state.leftWidth = `${Math.min(85, Math.max(15, current + step)).toFixed(1)}%`;
      this.apply();
    });
  }
}
