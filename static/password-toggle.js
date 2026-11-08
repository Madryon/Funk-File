/**
 * static/password-toggle.js
 * Universal, accessible Show/Hide Password Toggle component.
 * Supports all forms (login, signup, confirm password, reset password, admin).
 * Features:
 * - Real <button type="button"> with aria-label, aria-pressed, keyboard focusable.
 * - Eye and eye-slash SVG icons.
 * - Auto-hide after 30 seconds of being visible.
 * - Auto-hide on form submission.
 * - Touch-friendly 44px tap target.
 * - Theme-compatible via CSS variables.
 * - Preserves password manager autofill.
 */

(function () {
  'use strict';

  const SVG_EYE = '<svg class="eye-icon eye-open" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
  const SVG_EYE_SLASH = '<svg class="eye-icon eye-closed" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

  function enhancePasswordField(input) {
    if (!input || input.dataset.hasPasswordToggle === 'true') return;
    input.dataset.hasPasswordToggle = 'true';

    // Ensure the input is inside a .password-input-wrap
    let wrap = input.parentElement;
    if (!wrap || !wrap.classList.contains('password-input-wrap')) {
      wrap = document.createElement('div');
      wrap.className = 'password-input-wrap';
      input.parentNode.insertBefore(wrap, input);
      wrap.appendChild(input);
    }

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'password-toggle-btn';
    btn.setAttribute('aria-label', 'Show password');
    btn.setAttribute('aria-pressed', 'false');
    btn.setAttribute('tabindex', '0');
    btn.innerHTML = SVG_EYE;

    let autoHideTimer = null;

    const setVisible = (show) => {
      if (show) {
        input.type = 'text';
        btn.innerHTML = SVG_EYE_SLASH;
        btn.setAttribute('aria-label', 'Hide password');
        btn.setAttribute('aria-pressed', 'true');
        btn.classList.add('active');

        // Automatically switch back to hidden after 30 seconds
        if (autoHideTimer) clearTimeout(autoHideTimer);
        autoHideTimer = setTimeout(() => {
          setVisible(false);
        }, 30000);
      } else {
        input.type = 'password';
        btn.innerHTML = SVG_EYE;
        btn.setAttribute('aria-label', 'Show password');
        btn.setAttribute('aria-pressed', 'false');
        btn.classList.remove('active');
        if (autoHideTimer) {
          clearTimeout(autoHideTimer);
          autoHideTimer = null;
        }
      }
    };

    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const willShow = input.type === 'password';
      setVisible(willShow);
      input.focus();
    });

    // Auto switch back to hidden when form is submitted
    const form = input.closest('form');
    if (form) {
      form.addEventListener('submit', () => {
        setVisible(false);
      });
    }

    // Expose hide function on the input element for programmatic resets
    input.hidePassword = () => setVisible(false);

    wrap.appendChild(btn);
  }

  function initAll() {
    document.querySelectorAll('input[type="password"], input[data-is-password="true"]').forEach(enhancePasswordField);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }

  // Observe DOM for dynamic password fields (e.g. modals)
  if (typeof MutationObserver !== 'undefined') {
    const observer = new MutationObserver(() => {
      initAll();
    });
    observer.observe(document.body || document.documentElement, { childList: true, subtree: true });
  }

  window.initPasswordToggles = initAll;
})();
