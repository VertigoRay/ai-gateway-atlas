// Local clipboard helpers only. No network requests or vault access.
(() => {
  const prefix = document.body.dataset.domainTemplatePrefix;
  const suffix = prefix && location.hostname.startsWith(prefix + '.')
    ? location.hostname.slice(prefix.length) : '';
  const resolve = value => suffix ? value.replaceAll('{v}', suffix) : value;
  document.querySelectorAll('[data-display-value]').forEach(element => {
    element.textContent = resolve(element.dataset.displayValue);
  });
  const status = document.getElementById('copy-status');
  const timers = new WeakMap();
  async function copyText(value) {
    if (navigator.clipboard?.writeText) {
      try { await navigator.clipboard.writeText(value); return; } catch { /* Use selection fallback. */ }
    }
    const previous = document.activeElement;
    const input = document.createElement('textarea');
    input.value = value;
    input.className = 'clipboard-fallback';
    input.setAttribute('readonly', '');
    document.body.append(input);
    input.select();
    let copied = false;
    try { copied = document.execCommand('copy'); } finally { input.remove(); previous?.focus(); }
    if (!copied) throw Error('Clipboard unavailable');
  }
  document.querySelectorAll('[data-copy]').forEach(button => {
    const icon = button.innerHTML;
    const title = button.title;
    button.addEventListener('click', async () => {
      clearTimeout(timers.get(button));
      button.disabled = true;
      try {
        const value = resolve(button.dataset.copy);
        await copyText(value);
        button.textContent = '✓';
        button.title = 'Copied!';
        button.dataset.state = 'copied';
        status.textContent = `Copied ${value}${button.dataset.template ? '. Replace the example parts before saving.' : '.'}`;
      } catch {
        button.textContent = '!';
        button.title = 'Clipboard unavailable — select and copy the text';
        button.dataset.state = 'failed';
        status.textContent = 'Clipboard unavailable. Select the displayed text and copy it manually.';
      } finally {
        button.disabled = false;
        timers.set(button, setTimeout(() => { button.innerHTML = icon; button.title = title; delete button.dataset.state; }, 1800));
      }
    });
  });
})();
