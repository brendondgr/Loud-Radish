/* The transcript panel: the talk from start to finish, following the video like captions.
 *
 * Every line is a control. Clicking one seeks the player, which is what makes the transcript a way
 * to navigate the recording rather than a wall of text beside it — reading is how a person finds
 * the moment they want, not scrubbing.
 */

class Transcript {
  constructor(root, segments, player) {
    this.root = $("[data-transcript]", root);
    this.player = player;
    this.segments = segments;
    this.follow = $("[data-follow]", root);
    this.active = -1;

    this.lines = segments.map((segment) => this._line(segment));
    this.root.replaceChildren(...this.lines);

    if (!this.lines.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "This recording has no transcript.";
      this.root.replaceChildren(empty);
    }

    $("[data-transcript-search]", root)?.addEventListener("input", (event) => {
      this.highlight(event.target.value.trim().toLowerCase());
    });
  }

  /**
   * Move the marker to whatever line the video is inside.
   *
   * A binary search rather than a scan. `timeupdate` fires about four times a second and a
   * two-hour talk is a few thousand lines, so a linear pass is tens of thousands of comparisons a
   * second for an answer that changes every ten.
   */
  at(seconds) {
    let low = 0;
    let high = this.segments.length - 1;
    let found = -1;
    while (low <= high) {
      const middle = (low + high) >> 1;
      if (this.segments[middle].start <= seconds) {
        found = middle;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    if (found === this.active) return;

    if (this.active >= 0) this.lines[this.active].dataset.active = "false";
    const previous = this.active;
    this.active = found;
    if (found < 0) return;

    this.lines[found].dataset.active = "true";
    this._markPast(previous, found);

    if (this.follow?.checked) {
      this.lines[found].scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  highlight(query) {
    for (const [index, line] of this.lines.entries()) {
      const hit = Boolean(query) && this.segments[index].text.toLowerCase().includes(query);
      line.dataset.hit = String(hit);
    }
  }

  // -- internals -------------------------------------------------------------------

  _line(segment) {
    const line = document.createElement("div");
    line.className = "line";
    line.dataset.start = String(segment.start);

    const time = document.createElement("span");
    time.className = "line__time";
    time.textContent = clock(segment.start);

    const text = document.createElement("span");
    text.className = "line__text";
    text.textContent = segment.speaker ? `${segment.speaker}: ${segment.text}` : segment.text;

    line.append(time, text);
    line.addEventListener("click", () => this.player.seek(segment.start));
    return line;
  }

  /** Dim what has been said. Only the lines between the old and new positions are touched —
   *  rewriting every line's state on each move is what makes a long transcript stutter. */
  _markPast(from, to) {
    if (from < 0) {
      for (let index = 0; index < to; index += 1) this.lines[index].dataset.past = "true";
      return;
    }
    const [low, high] = from < to ? [from, to] : [to, from];
    for (let index = low; index <= high; index += 1) {
      this.lines[index].dataset.past = String(index < to);
    }
  }
}
