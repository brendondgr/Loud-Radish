/**
 * How a recording is named and dated on a lab card (D-080).
 *
 * **Dates are always read from `started_at`, never from the stored title.** Stored titles were
 * written as "Session on 07 Nov 2026, 17:11" in UTC at the moment a session began, so on a machine
 * five hours behind UTC every one of them is five hours wrong. `started_at` carries its offset and
 * the browser turns it into local time.
 */

const DEFAULT_TITLE = /^(Session|Recording) on \d/;

const DAY = 24 * 60 * 60 * 1000;

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function clock(date) {
  return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function partOfDay(date) {
  const hour = date.getHours();
  if (hour < 5) return "night";
  if (hour < 12) return "morning";
  if (hour < 17) return "afternoon";
  if (hour < 21) return "evening";
  return "night";
}

export function startedAt(session) {
  const date = new Date(session.started_at);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDate(date, style, now = new Date()) {
  if (!date) return "";
  const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY);
  const sameYear = date.getFullYear() === now.getFullYear();
  if (style === "relative") return relative(date, now);
  if (style === "absolute") {
    return date.toLocaleString(undefined, {
      weekday: "short",
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }
  if (style === "daypart") {
    const weekday = date.toLocaleDateString(undefined, { weekday: "long" });
    const day = date.toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
      ...(sameYear ? {} : { year: "numeric" }),
    });
    const name = days === 0 ? "Today" : days === 1 ? "Yesterday" : weekday;
    return `${name} ${partOfDay(date)} · ${day}`;
  }
  // Smart: the less the date needs saying, the less of it is said.
  if (days === 0) return `Today · ${clock(date)}`;
  if (days === 1) return `Yesterday · ${clock(date)}`;
  if (days > 1 && days < 7) {
    return `${date.toLocaleDateString(undefined, { weekday: "short" })} · ${clock(date)}`;
  }
  const day = date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
  });
  return `${day} · ${clock(date)}`;
}

function relative(date, now) {
  const seconds = Math.round((now - date) / 1000);
  const format = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const steps = [
    [60, "second", 1],
    [3600, "minute", 60],
    [86400, "hour", 3600],
    [604800, "day", 86400],
    [2629800, "week", 604800],
    [31557600, "month", 2629800],
  ];
  for (const [limit, unit, size] of steps) {
    if (seconds < limit) return format.format(-Math.round(seconds / size), unit);
  }
  return format.format(-Math.round(seconds / 31557600), "year");
}

/**
 * The card's title for a title style. Falls back down a fixed order — what was asked for, then
 * the keywords, then the opening words, then the date — so no card is ever blank.
 *
 * Returns `{ text, auto }`: `auto` marks a title the page made up rather than one the user gave.
 */
export function cardTitle(session, glance, style) {
  const stored = session.title && !DEFAULT_TITLE.test(session.title) ? session.title : "";
  const words = (text, count) => {
    const list = text.replace(/[“”"…]/g, "").split(/\s+/).filter(Boolean);
    return list.slice(0, count).join(" ") + (list.length > count ? "…" : "");
  };
  const keywords = glance?.keywords?.length ? glance.keywords.join(" · ") : "";
  const opening = glance?.opening ? words(glance.opening, 8) : "";
  const summary = glance?.summary ? words(glance.summary, 9) : "";
  const date = formatDate(startedAt(session), "daypart");

  const candidates = {
    stored: [stored, session.title],
    keywords: [stored, keywords, opening],
    summary: [stored, summary, keywords, opening],
    opening: [stored, opening],
    date: [date],
  }[style] ?? [stored];

  for (const text of candidates) {
    if (text) return { text, auto: text !== stored && text !== session.title };
  }
  if (session.orphan) return { text: "Recording with no transcript", auto: true };
  if (!session.segments) return { text: "Nothing was said", auto: true };
  return { text: date || "Untitled recording", auto: true };
}

export function snippet(glance) {
  return glance?.summary || glance?.opening || "";
}

export function duration(seconds) {
  const total = Math.round(seconds || 0);
  if (total < 60) return `${total}s`;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`
    : `${minutes}:${String(rest).padStart(2, "0")}`;
}

export function bytes(size) {
  if (!size) return "0 KB";
  if (size >= 1024 ** 3) return `${(size / 1024 ** 3).toFixed(1)} GB`;
  if (size >= 1024 ** 2) return `${(size / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(size / 1024))} KB`;
}

/** The heading a recording is grouped under, and a sortable key for it. */
export function groupOf(session, by, now = new Date()) {
  const date = startedAt(session);
  if (!date || by === "none") return { key: "all", label: "" };
  if (by === "month") {
    return {
      key: `${date.getFullYear()}-${date.getMonth()}`,
      label: date.toLocaleDateString(undefined, { month: "long", year: "numeric" }),
    };
  }
  if (by === "week") {
    const mondayOf = (day) => {
      const start = startOfDay(day);
      start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
      return start;
    };
    const monday = mondayOf(date);
    const weeks = Math.round((mondayOf(now) - monday) / (7 * DAY));
    const label =
      weeks <= 0
        ? "This week"
        : weeks === 1
          ? "Last week"
          : `Week of ${monday.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`;
    return { key: monday.toISOString(), label };
  }
  const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY);
  const label =
    days === 0
      ? "Today"
      : days === 1
        ? "Yesterday"
        : date.toLocaleDateString(undefined, {
            weekday: "long",
            day: "numeric",
            month: "long",
            ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
          });
  return { key: startOfDay(date).toISOString(), label };
}

/** Hue for a recording's time of day, for the "colour" placeholder: dawn amber to night blue. */
export function hourHue(session) {
  const hour = startedAt(session)?.getHours() ?? 12;
  return [230, 230, 230, 240, 250, 30, 35, 40, 45, 170, 175, 180, 185, 190, 195, 200, 25, 15, 5, 280, 260, 250, 240, 235][hour];
}
