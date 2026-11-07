/**
 * The lab's icons (D-082): the interface's shared set (`core/icons.js`), boxed as `.lab-icon`.
 */

import { iconNode as coreIconNode } from "../core/icons.js";

export { icon } from "../core/icons.js";

export function iconNode(name, { size = 16, className = "lab-icon" } = {}) {
  return coreIconNode(name, { size, className });
}
