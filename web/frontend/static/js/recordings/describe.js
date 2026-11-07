/**
 * How a recording is named and dated on the Recordings page (D-085).
 *
 * **Dates are read from `started_at`, never from the title.** A made-up title ("Session on 07 Nov
 * 2026, 17:11") is built by the server from the start time; `started_at` carries its offset and the
 * browser says it in local time, so a card can never disagree with the clock on the wall.
 */

const DAY = 24 * 60 * 60 * 1000;

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function clock(date) {
  return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export function startedAt(session) {
  const date = new Date(session.started_at);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "Today · 12:11 PM", "Mon · 3:04 PM", "Sep 18 · 12:11 PM": the less a date needs, the less is said. */
export function smartDate(date, now = new Date()) {
  if (!date) return "";
  const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY);
  if (days === 0) return `Today · ${clock(date)}`;
  if (days === 1) return `Yesterday · ${clock(date)}`;
  if (days > 1 && days < 7) {
    return `${date.toLocaleDateString(undefined, { weekday: "short" })} · ${clock(date)}`;
  }
  const day = date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
  return `${day} · ${clock(date)}`;
}

/** The whole date, for the compact window's subtitle. */
export function fullDate(date) {
  if (!date) return "";
  return date.toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/**
 * The card's title: the one the owner or the model gave (D-084), else the transcript's keywords,
 * else its opening words — so no card is blank and none says "Session on <date>".
 *
 * Returns `{ text, auto }`: `auto` marks a title made up here, drawn with a spark.
 */
export function cardTitle(session, glance) {
  if (session.named && session.title) return { text: session.title, auto: false };
  if (glance?.keywords?.length) return { text: glance.keywords.slice(0, 3).join(" · "), auto: true };
  if (glance?.opening) {
    const words = glance.opening.replace(/[“”"…]/g, "").split(/\s+/).filter(Boolean);
    return { text: words.slice(0, 8).join(" ") + (words.length > 8 ? "…" : ""), auto: true };
  }
  if (!session.segments) {
    return { text: session.media?.transcribable ? "Not transcribed yet" : "Nothing was said", auto: true };
  }
  return { text: session.title || "Untitled recording", auto: !session.named };
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

/** Everything the recording takes on disk: its database and its folder. */
export const onDisk = (session) => (session.size_bytes ?? 0) + (session.media?.recording_bytes ?? 0);

/** The day a recording is grouped under: "Today", "Yesterday", or "Monday 3 November". */
export function dayOf(session, now = new Date()) {
  const date = startedAt(session);
  if (!date) return { key: "undated", label: "Undated" };
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

/** A hue for the time of day, drawn where there is no video: dawn amber to night blue. */
export function hourHue(session) {
  const hour = startedAt(session)?.getHours() ?? 12;
  return [
    230, 230, 230, 240, 250, 30, 35, 40, 45, 170, 175, 180, 185, 190, 195, 200, 25, 15, 5, 280, 260,
    250, 240, 235,
  ][hour];
}
