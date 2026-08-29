/* The video panel: a full set of transport controls, and the clock the transcript follows.
 *
 * The controls are the application's own rather than the browser's default set. `controls` on a
 * `<video>` gives play, a scrub bar and fullscreen and nothing else — no ten-second skip, which is
 * the single most-used control when someone is trying to catch a sentence they missed, and the
 * whole reason to re-watch a talk at all.
 */

const SKIP_SECONDS = 10;

class Player {
  constructor(root, media) {
    this.root = root;
    this.video = $("[data-video]", root);
    this.video.src = media.src;
    if (media.type) this.video.setAttribute("type", media.type);

    this.scrub = $("[data-scrub]", root);
    this.time = $("[data-time]", root);
    this.playButton = $("[data-play]", root);
    this.fullscreenButton = $("[data-fullscreen]", root);

    /** Called on every position change. The transcript subscribes to it. */
    this.onSeek = () => {};
    this.scrubbing = false;

    this._wireTransport();
    this._wireScrub();
    this._wireKeys();

    this.video.addEventListener("timeupdate", () => this._render());
    this.video.addEventListener("loadedmetadata", () => this._render());
    this.video.addEventListener("play", () => (this.playButton.textContent = "❚❚"));
    this.video.addEventListener("pause", () => (this.playButton.textContent = "▶"));
    this.video.addEventListener("error", () => this._reportMissingMedia());
  }

  get currentTime() {
    return this.video.currentTime;
  }

  toggle() {
    if (this.video.paused) void this.video.play();
    else this.video.pause();
  }

  nudge(seconds) {
    this.seek(this.video.currentTime + seconds);
  }

  seek(seconds) {
    const duration = this.video.duration;
    const limit = Number.isFinite(duration) ? duration : Number.MAX_SAFE_INTEGER;
    this.video.currentTime = Math.min(limit, Math.max(0, seconds));
  }

  // -- internals -------------------------------------------------------------------

  _wireTransport() {
    this.playButton.addEventListener("click", () => this.toggle());
    $("[data-back]", this.root).addEventListener("click", () => this.nudge(-SKIP_SECONDS));
    $("[data-forward]", this.root).addEventListener("click", () => this.nudge(SKIP_SECONDS));
    $("[data-rate]", this.root).addEventListener("change", (event) => {
      this.video.playbackRate = Number(event.target.value);
    });

    const mute = $("[data-mute]", this.root);
    mute.addEventListener("click", () => {
      this.video.muted = !this.video.muted;
      mute.textContent = this.video.muted ? "🔇" : "🔊";
      mute.title = this.video.muted ? "Unmute" : "Mute";
    });

    this.fullscreenButton.addEventListener("click", () => {
      if (document.fullscreenElement) void document.exitFullscreen();
      else void $("[data-player]", this.root)?.requestFullscreen?.();
    });
  }

  _wireScrub() {
    // Scrubbing is *live* — the frame follows the handle rather than waiting for release, because
    // finding a moment in a talk means recognising it on screen.
    this.scrub.addEventListener("pointerdown", () => (this.scrubbing = true));
    this.scrub.addEventListener("pointerup", () => (this.scrubbing = false));
    this.scrub.addEventListener("input", () => {
      const duration = this.video.duration;
      if (Number.isFinite(duration)) this.video.currentTime = (this.scrub.value / 1000) * duration;
    });
  }

  _wireKeys() {
    // On the document rather than the player: the user is usually reading the transcript when they
    // want to pause, and a shortcut that needs the video focused first is one nobody reaches for.
    document.addEventListener("keydown", (event) => {
      const tag = event.target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      if (event.key === " ") {
        event.preventDefault();
        this.toggle();
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        this.nudge(-SKIP_SECONDS);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        this.nudge(SKIP_SECONDS);
      } else if (event.key === "f" || event.key === "F") {
        this.fullscreenButton.click();
      }
    });
  }

  _render() {
    const { currentTime, duration } = this.video;
    if (!this.scrubbing && Number.isFinite(duration) && duration > 0) {
      this.scrub.value = String(Math.round((currentTime / duration) * 1000));
    }
    this.time.textContent = `${clock(currentTime)} / ${clock(duration)}`;
    this.onSeek(currentTime);
  }

  _reportMissingMedia() {
    // A `<video>` that cannot load its source renders as a black rectangle, which is
    // indistinguishable from a recording of a dark screen. Saying so is the whole difference.
    const message = document.createElement("p");
    message.className = "empty";
    message.style.padding = "var(--space-8)";
    message.textContent =
      `The video file could not be played. Check that ${this.video.getAttribute("src")} is ` +
      "still beside this page, and that your browser supports the format it was recorded in.";
    $("[data-player]", this.root)?.replaceChildren(message);
  }
}
