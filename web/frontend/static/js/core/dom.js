/** Small DOM helpers. Nothing here knows about the application. */

/** Query one element within `root`. */
export const $ = (selector, root = document) => root.querySelector(selector);

/** Query all elements within `root`, as a real array. */
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/**
 * Create an element.
 *
 * `text` is assigned via textContent, never innerHTML: transcript text is model output and must
 * never be interpreted as markup.
 */
export function el(tag, { className, text, attrs, children } = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  if (attrs) {
    for (const [name, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      node.setAttribute(name, value === true ? "" : String(value));
    }
  }
  if (children) node.append(...children.filter(Boolean));
  return node;
}

/** Show or hide an element via the `hidden` attribute. */
export function toggle(node, visible) {
  if (node) node.hidden = !visible;
}

/** Set textContent only when it differs, to avoid needless layout work. */
export function setText(node, text) {
  if (node && node.textContent !== text) node.textContent = text;
}

/** Set an attribute only when it differs. */
export function setAttr(node, name, value) {
  if (!node) return;
  if (node.getAttribute(name) !== String(value)) node.setAttribute(name, String(value));
}

/** Instantiate a `<template>` by id and return its first element. */
export function fromTemplate(id) {
  const template = document.getElementById(id);
  if (!template) throw new Error(`No template with id "${id}"`);
  return template.content.firstElementChild.cloneNode(true);
}
