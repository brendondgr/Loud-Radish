/**
 * The lab's icons, as SVG source (D-080).
 *
 * Fixed strings written here, never built from data, so inserting them as markup is safe. Every
 * one is decorative: the button or label around it carries the accessible name.
 */

const svg = (body, size = 16) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 16 16" fill="none" stroke="currentColor" ` +
  `stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

const PATHS = {
  video: '<rect x="1.5" y="3.5" width="9" height="9" rx="1.6"/><path d="m10.5 7 4-2.3v6.6l-4-2.3"/>',
  audio: '<path d="M2 7v2M4.5 5v6M7 3v10M9.5 5.5v5M12 4v8M14.5 7v2"/>',
  transcript: '<path d="M4 1.8h5.4L12.6 5v9.2H4z"/><path d="M9.2 1.8V5h3.4M6 8h4.6M6 10.5h4.6M6 13h2.6"/>',
  main: '<rect x="1.5" y="2.5" width="13" height="9" rx="1.4"/><path d="M5.5 14h5M8 11.5V14M6 7l2-2 2 2M8 5v4"/>',
  ask: '<path d="M2.2 3h11.6v7.8H7.4l-3 2.6v-2.6H2.2z"/><path d="M6.4 5.6a1.7 1.7 0 1 1 2.3 1.6c-.5.2-.7.5-.7 1M8 9.4v.1"/>',
  download: '<path d="M8 2v8.5M4.6 7.4 8 10.8l3.4-3.4M2.5 13.5h11"/>',
  folder: '<path d="M1.8 4.2c0-.7.5-1.2 1.2-1.2h3l1.5 1.6H13c.7 0 1.2.5 1.2 1.2v6.5c0 .7-.5 1.2-1.2 1.2H3c-.7 0-1.2-.5-1.2-1.2z"/>',
  webapp: '<rect x="1.5" y="2.5" width="13" height="11" rx="1.4"/><path d="M1.5 5.5h13M3.6 4h.1M5.4 4h.1M6 8.3 4.6 9.7 6 11.1M10 8.3l1.4 1.4-1.4 1.4"/>',
  more: '<circle cx="3.5" cy="8" r=".6" fill="currentColor"/><circle cx="8" cy="8" r=".6" fill="currentColor"/><circle cx="12.5" cy="8" r=".6" fill="currentColor"/>',
  trash: '<path d="M2.5 4.2h11M6 4.2V2.6h4v1.6M3.8 4.2l.7 9.2h7l.7-9.2M6.6 6.8v4.4M9.4 6.8v4.4"/>',
  spark: '<path d="M8 1.8 9.3 6.7l4.9 1.3-4.9 1.3L8 14.2 6.7 9.3 1.8 8l4.9-1.3z"/>',
  wand: '<path d="m2 14 8.2-8.2M9 3.2V1.6M12.8 7h1.6M11.6 4.4l1.1-1.1M9.6 5.2l1.2 1.2"/>',
  close: '<path d="m3.5 3.5 9 9M12.5 3.5l-9 9"/>',
  search: '<circle cx="7" cy="7" r="4.3"/><path d="m10.3 10.3 3.4 3.4"/>',
  sliders: '<path d="M2 4.5h7M12 4.5h2M2 11.5h2M7 11.5h7"/><circle cx="10.5" cy="4.5" r="1.5"/><circle cx="5.5" cy="11.5" r="1.5"/>',
  copy: '<rect x="5" y="5" width="9" height="9" rx="1.4"/><path d="M11 5V3.2c0-.7-.5-1.2-1.2-1.2H3.2C2.5 2 2 2.5 2 3.2v6.6c0 .7.5 1.2 1.2 1.2H5"/>',
  heart: '<path d="M8 13.6S2 10.1 2 5.9A3 3 0 0 1 8 4.4a3 3 0 0 1 6 1.5c0 4.2-6 7.7-6 7.7z"/>',
  up: '<path d="M5 14V7.5M5 7.5 7.8 2c1.2 0 2 .9 1.8 2.1L9.2 6h3.6c.8 0 1.4.8 1.2 1.6l-1.2 5c-.2.8-.8 1.4-1.6 1.4H5M2 7.5h3V14H2z"/>',
  down: '<path d="M11 2v6.5M11 8.5 8.2 14c-1.2 0-2-.9-1.8-2.1l.4-1.9H3.2c-.8 0-1.4-.8-1.2-1.6l1.2-5C3.4 2.6 4 2 4.8 2H11M14 8.5h-3V2h3z"/>',
  back: '<path d="M13.5 8h-11M6.5 4 2.5 8l4 4"/>',
  check: '<path d="m3 8.5 3.2 3L13 4.5"/>',
  "layout-gallery": '<rect x="1.5" y="1.5" width="5.5" height="5.5" rx="1"/><rect x="9" y="1.5" width="5.5" height="5.5" rx="1"/><path d="M1.5 9.5h5.5M9 9.5h5.5M1.5 12h4M9 12h4"/>',
  "layout-tiles": '<rect x="1.5" y="1.5" width="5.5" height="5.5" rx="1"/><rect x="9" y="1.5" width="5.5" height="5.5" rx="1"/><rect x="1.5" y="9" width="5.5" height="5.5" rx="1"/><rect x="9" y="9" width="5.5" height="5.5" rx="1"/>',
  "layout-wide": '<rect x="1.5" y="2" width="5" height="4.5" rx="1"/><rect x="1.5" y="9.5" width="5" height="4.5" rx="1"/><path d="M8.5 3h6M8.5 5.5h4M8.5 10.5h6M8.5 13h4"/>',
  "layout-timeline": '<path d="M3 1.5v13"/><circle cx="3" cy="4" r="1.2" fill="currentColor"/><circle cx="3" cy="11" r="1.2" fill="currentColor"/><rect x="6" y="2" width="3.5" height="4" rx=".8"/><rect x="11" y="2" width="3.5" height="4" rx=".8"/><rect x="6" y="9" width="3.5" height="4" rx=".8"/>',
  "layout-split": '<rect x="1.5" y="2" width="13" height="12" rx="1.4"/><path d="M8.5 2v12M3.5 5h3M3.5 8h3M3.5 11h3"/>',
  "layout-list": '<rect x="1.5" y="2.5" width="3" height="2.5" rx=".6"/><rect x="1.5" y="6.8" width="3" height="2.5" rx=".6"/><rect x="1.5" y="11" width="3" height="2.5" rx=".6"/><path d="M6.5 3.7h8M6.5 8h8M6.5 12.3h8"/>',
  play: '<path d="M5 3.2v9.6L12.6 8z" fill="currentColor" stroke="none"/>',
};

export function icon(name, size = 16) {
  return svg(PATHS[name] ?? "", size);
}

/** An element holding an icon, ready to go inside a button or a label. */
export function iconNode(name, { size = 16, className = "lab-icon" } = {}) {
  const span = document.createElement("span");
  span.className = className;
  span.innerHTML = icon(name, size);
  return span;
}
