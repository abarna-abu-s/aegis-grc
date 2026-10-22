import { clear, h } from '../ui.js';

// Tabs whose content is built lazily when first selected. `initial` selects a tab by key.
export function tabbed(tabs, initial, onChange) {
  const body = h('div', {});
  const bar = h('div', { class: 'tabs', role: 'tablist' });
  const built = {};
  function select(key) {
    [...bar.children].forEach((b) => { b.classList.toggle('active', b.dataset.key === key); b.setAttribute('aria-selected', b.dataset.key === key); });
    clear(body);
    const t = tabs.find((x) => x[0] === key) || tabs[0];
    body.append(built[t[0]] ||= t[2]());
    if (onChange) onChange(t[0]);
  }
  tabs.forEach(([key, text]) => bar.append(h('button', { role: 'tab', 'data-key': key, onClick: () => { delete built[key]; select(key); } }, text)));
  select(initial && tabs.some((t) => t[0] === initial) ? initial : tabs[0][0]);
  return h('div', {}, bar, body);
}
