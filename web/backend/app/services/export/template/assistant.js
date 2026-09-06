/* The Q&A panel: questions about the talk, answered by a language model on the user's own machine.
 *
 * **The transcript is the evidence; the video is addressed through it.** A local text model cannot
 * watch a recording, so a question about what was on screen is answered from what was said and
 * cited by timestamp — and every citation the model writes becomes a control that seeks the
 * player. That is the difference between "what did the diagram halfway through show" being a
 * question with a useful answer and one with an invented one.
 *
 * The model settings live in this panel rather than behind an application-level dialog, because
 * there is no application around this page. A setting that has to be found somewhere else is a
 * setting nobody changes.
 */

/**
 * The transcript lines nearest the current moment, up to a character budget.
 *
 * Characters, not tokens: this page has no tokeniser, and a budget it can actually enforce beats
 * one it can only estimate. Centred on where the viewer is, because that is what a question is
 * almost always about — the window grows outwards from there until the budget runs out.
 */
function contextAround(segments, seconds, budget) {
  const lines = segments.map((segment) => `[${clock(segment.start)}] ${segment.text}`);
  const whole = lines.join("\n");
  if (whole.length <= budget) return whole;

  let index = lines.findIndex((_, position) => segments[position].start > seconds);
  if (index < 0) index = lines.length - 1;

  let start = index;
  let end = index;
  let used = lines[index]?.length ?? 0;
  while (start > 0 || end < lines.length - 1) {
    if (start > 0 && used + lines[start - 1].length < budget) {
      start -= 1;
      used += lines[start].length;
    } else if (end < lines.length - 1 && used + lines[end + 1].length < budget) {
      end += 1;
      used += lines[end].length;
    } else {
      break;
    }
  }
  return lines.slice(start, end + 1).join("\n");
}

/** How many previous turns to resend. Enough for a follow-up; not enough to crowd out transcript. */
const HISTORY_TURNS = 8;

class Assistant {
  constructor(root, { data, settings, player }) {
    this.root = root;
    this.data = data;
    this.player = player;

    this.log = $("[data-chat]", root);
    this.status = $("[data-chat-status]", root);
    this.question = $("[data-question]", root);
    this.askButton = $("[data-ask]", root);

    this.saved = scopedStore("llm");
    this.settings = { ...settings.llm, ...this.saved.read({}) };
    this.context = { ...settings.context };
    this.system = settings.prompts.system;
    this.pending = false;

    // **Usually empty, and that is the default.** An export the user keeps can carry the
    // conversation they had during the talk, so the page opens where they left off. One they send
    // to somebody else does not: the point of this panel is that the recipient connects their own
    // model and asks their own questions, and finding it half-full of another person's questions
    // about a talk they have not watched yet is the opposite of that.
    this.history = (data.chat ?? [])
      .filter((message) => message.role === "user" || message.role === "assistant")
      .map((message) => ({ role: message.role, content: message.text }));

    this._wireSettings();
    this._wireQuickActions(settings.quick_actions ?? []);
    this._wireComposer();
    this.render();
  }

  // -- rendering -------------------------------------------------------------------

  render() {
    if (!this.history.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent =
        "Ask anything about this talk. Answers come from the transcript, and every timestamp in " +
        "one is a link that jumps the video there.";
      this.log.replaceChildren(empty);
      return;
    }
    this.log.replaceChildren(...this.history.map((message) => this._bubble(message)));
    this.log.scrollTop = this.log.scrollHeight;
  }

  _bubble(message) {
    const node = document.createElement("div");
    node.className = "message";
    node.dataset.role = message.role;

    const role = document.createElement("span");
    role.className = "message__role";
    role.textContent =
      message.role === "user" ? "You" : message.role === "error" ? "Problem" : "Assistant";

    const text = document.createElement("div");
    text.className = "message__text";
    this._withCitations(text, message.content);

    node.append(role, text);
    return node;
  }

  /** Turn every `[MM:SS]` the model wrote into a control that seeks the player. */
  _withCitations(node, content) {
    const pattern = /\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g;
    let last = 0;
    let match = pattern.exec(content);
    while (match !== null) {
      node.append(content.slice(last, match.index));
      const seconds = parseClock(match[1]);
      if (seconds === null) {
        node.append(match[0]);
      } else {
        const cite = document.createElement("button");
        cite.type = "button";
        cite.className = "cite";
        cite.textContent = match[1];
        cite.title = "Jump the video here";
        cite.addEventListener("click", () => this.player.seek(seconds));
        node.append(cite);
      }
      last = pattern.lastIndex;
      match = pattern.exec(content);
    }
    node.append(content.slice(last));
  }

  // -- asking ----------------------------------------------------------------------

  async ask(question) {
    if (!question || this.pending) return;
    if (!this.settings.model) {
      this._say("Set a model name in this panel's settings first.", "error");
      $("[data-model-settings]", this.root).hidden = false;
      return;
    }

    this.pending = true;
    this.askButton.disabled = true;
    this.question.value = "";
    this.history.push({ role: "user", content: question });
    this.render();
    this._say("Thinking…");

    try {
      this.history.push({ role: "assistant", content: await this._request(question) });
      this._say("");
    } catch (error) {
      this.history.push({ role: "error", content: this._explain(error) });
      this._say("The request did not reach the model.", "error");
    } finally {
      this.pending = false;
      this.askButton.disabled = false;
      this.render();
    }
  }

  async _request(question) {
    const endpoint = String(this.settings.endpoint || "").replace(/\/+$/, "");
    const headers = { "Content-Type": "application/json" };
    if (this.settings.api_key) headers.Authorization = `Bearer ${this.settings.api_key}`;

    const response = await fetch(`${endpoint}/chat/completions`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        model: this.settings.model,
        temperature: Number(this.settings.temperature ?? 0.3),
        max_tokens: Number(this.settings.max_output_tokens ?? 1024),
        stream: false,
        messages: [
          { role: "system", content: this._systemMessage() },
          // The conversation only. The context block is rebuilt fresh each time and never kept in
          // the history: re-sending an hour-old window of transcript with every question spends
          // the budget the current one needs.
          ...this.history
            .filter((message) => message.role !== "error")
            .slice(-HISTORY_TURNS)
            .filter((message) => message.content !== question),
          { role: "user", content: question },
        ],
      }),
    });

    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      throw new Error(`${response.status} ${detail}`.trim());
    }
    const body = await response.json();
    const answer = body?.choices?.[0]?.message?.content;
    if (!answer) throw new Error("The model returned no text.");
    return answer;
  }

  _systemMessage() {
    const session = this.data.session;
    const parts = [
      this.system,
      "",
      "## About this talk",
      `Title: ${session.title}`,
      session.speaker ? `Speaker: ${session.speaker}` : "",
      `Length: ${clock(session.duration_seconds)}`,
      `The viewer is currently at [${clock(this.player.currentTime)}].`,
    ].filter(Boolean);

    if (this.context.include_summaries && this.data.summaries?.length) {
      parts.push(
        "",
        "## Summary of the talk (compressed — not the speaker's words)",
        ...this.data.summaries.map((summary) => summary.text)
      );
    }
    if (this.context.include_glossary && this.data.glossary?.length) {
      parts.push(
        "",
        "## Terms introduced in this talk",
        ...this.data.glossary.map((term) => `${term.term}: ${term.definition}`)
      );
    }
    parts.push(
      "",
      "## Transcript (verbatim)",
      contextAround(
        this.data.segments,
        this.player.currentTime,
        Number(this.context.max_transcript_chars) || 24000
      )
    );
    return parts.join("\n");
  }

  _explain(error) {
    const message = String(error?.message ?? error);
    // A browser reports a blocked cross-origin request as an opaque "Failed to fetch" — the most
    // likely failure here, and the one whose remedy is least guessable.
    if (/failed to fetch|networkerror|load failed/i.test(message)) {
      return (
        `Could not reach ${this.settings.endpoint}. Either the model server is not running, or ` +
        'it is refusing this page\'s origin. For Ollama, start it with OLLAMA_ORIGINS="*".'
      );
    }
    return message;
  }

  _say(text, tone = "") {
    this.status.textContent = text;
    this.status.dataset.tone = tone;
  }

  // -- wiring ----------------------------------------------------------------------

  _wireSettings() {
    const fields = {
      "[data-llm-endpoint]": "endpoint",
      "[data-llm-model]": "model",
      "[data-llm-key]": "api_key",
      "[data-llm-temperature]": "temperature",
      "[data-llm-max-tokens]": "max_output_tokens",
    };
    for (const [selector, key] of Object.entries(fields)) {
      const input = $(selector, this.root);
      input.value = this.settings[key] ?? "";
      input.addEventListener("change", () => {
        this.settings[key] = input.type === "number" ? Number(input.value) : input.value;
        this.saved.write(this.settings);
      });
    }

    const context = $("[data-llm-context]", this.root);
    context.value = this.context.max_transcript_chars;
    context.addEventListener("change", () => {
      this.context.max_transcript_chars = Number(context.value);
    });

    $("[data-open-model]", this.root).addEventListener("click", () => {
      const panel = $("[data-model-settings]", this.root);
      panel.hidden = !panel.hidden;
    });

    // Opened on first run when no model is set. A panel that fails the first question with
    // "model not found" teaches nothing about where to fix it.
    if (!this.settings.model) $("[data-model-settings]", this.root).hidden = false;
  }

  _wireQuickActions(actions) {
    $("[data-quick-actions]", this.root).replaceChildren(
      ...actions.map((action) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "button button--quiet";
        button.textContent = action.label;
        button.title = action.prompt;
        button.addEventListener("click", () => void this.ask(action.prompt));
        return button;
      })
    );
  }

  _wireComposer() {
    $("[data-clear-chat]", this.root).addEventListener("click", () => {
      this.history = [];
      this.render();
    });
    $("[data-ask-form]", this.root).addEventListener("submit", (event) => {
      event.preventDefault();
      void this.ask(this.question.value.trim());
    });
    // Enter sends, Shift+Enter is a newline. The composer is two rows, so a question needing a
    // second line is rare and a send that needs the mouse is not.
    this.question.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void this.ask(this.question.value.trim());
      }
    });
  }
}
