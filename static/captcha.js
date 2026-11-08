/**
 * static/captcha.js
 * Frontend CAPTCHA component & hook implementation for Funk Files.
 *
 * Provides:
 * - CaptchaCheckbox
 * - CaptchaModal
 * - useCaptcha
 * Supports 'custom' (default 3x3 / 4x4 grid), 'turnstile', and 'recaptcha'.
 * Fully accessible, touch-friendly, keyboard navigable, and theme-token styled.
 */

(function () {
  'use strict';

  // SVG Icons
  const ICONS = {
    check: '<svg class="captcha-check-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
    shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg>',
    headphones: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 18v-6a9 9 0 0118 0v6M21 19a2 2 0 01-2 2h-1a2 2 0 01-2-2v-3a2 2 0 012-2h3zM3 19a2 2 0 002 2h1a2 2 0 002-2v-3a2 2 0 00-2-2H3z"/></svg>',
    image: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>',
    play: '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>'
  };

  /**
   * CaptchaCheckbox Component
   */
  class CaptchaCheckbox {
    constructor(options = {}) {
      this.container = typeof options.container === 'string'
        ? document.querySelector(options.container)
        : options.container;

      this.onClick = options.onClick || (() => {});
      this.inputName = options.inputName || 'captchaToken';
      this.token = '';
      this.isVerified = false;
      this.isLoading = false;

      this.render();
    }

    render() {
      if (!this.container) return;
      this.container.innerHTML = `
        <div class="captcha-widget-wrap">
          <input type="text" name="b_hp_check" style="display:none !important; position:absolute; left:-9999px;" tabindex="-1" autocomplete="off" aria-hidden="true">
          <input type="hidden" name="${this.inputName}" class="captcha-token-hidden" value="">
          <div class="captcha-checkbox-card">
            <div class="captcha-left-side" role="button" tabindex="0" aria-label="I'm not a robot security verification">
              <div class="captcha-box" role="checkbox" aria-checked="false" tabindex="-1">
                <div class="captcha-spinner"></div>
                ${ICONS.check}
              </div>
              <span class="captcha-label">I'm not a robot</span>
            </div>
            <div class="captcha-brand-badge" aria-hidden="true">
              ${ICONS.shield}
              <span>Security</span>
            </div>
          </div>
        </div>
      `;

      this.box = this.container.querySelector('.captcha-box');
      this.card = this.container.querySelector('.captcha-checkbox-card');
      this.clickable = this.container.querySelector('.captcha-left-side');
      this.hiddenInput = this.container.querySelector('.captcha-token-hidden');

      const trigger = () => {
        if (this.isVerified || this.isLoading) return;
        this.onClick();
      };

      this.clickable.addEventListener('click', trigger);
      this.clickable.addEventListener('keydown', (e) => {
        if (e.key === ' ' || e.key === 'Enter') {
          e.preventDefault();
          trigger();
        }
      });
    }

    setLoading(loading) {
      this.isLoading = loading;
      if (this.box) {
        this.box.classList.toggle('loading', loading);
      }
    }

    setVerified(token) {
      this.token = token;
      this.isVerified = true;
      this.isLoading = false;
      if (this.box) {
        this.box.classList.remove('loading');
        this.box.classList.add('verified');
        this.box.setAttribute('aria-checked', 'true');
      }
      if (this.hiddenInput) {
        this.hiddenInput.value = token;
      }
      const label = this.container.querySelector('.captcha-label');
      if (label) label.textContent = 'Verified';
    }

    reset() {
      this.token = '';
      this.isVerified = false;
      this.isLoading = false;
      if (this.box) {
        this.box.classList.remove('loading', 'verified');
        this.box.setAttribute('aria-checked', 'false');
      }
      if (this.hiddenInput) {
        this.hiddenInput.value = '';
      }
      const label = this.container.querySelector('.captcha-label');
      if (label) label.textContent = "I'm not a robot";
    }
  }

  /**
   * CaptchaModal Component
   */
  class CaptchaModal {
    constructor(options = {}) {
      this.onSuccess = options.onSuccess || (() => {});
      this.onClose = options.onClose || (() => {});
      this.challenge = null;
      this.selectedIndexes = new Set();
      this.mode = 'image'; // 'image' or 'audio'
      this.audioObj = null;

      this.createDom();
    }

    createDom() {
      this.backdrop = document.createElement('div');
      this.backdrop.className = 'captcha-modal-backdrop';
      this.backdrop.setAttribute('role', 'dialog');
      this.backdrop.setAttribute('aria-modal', 'true');
      this.backdrop.setAttribute('aria-label', 'Security Verification');

      this.backdrop.innerHTML = `
        <div class="captcha-modal-card">
          <div class="captcha-header">
            <div class="captcha-header-text">
              <div class="captcha-header-sub">Select all images with</div>
              <div class="captcha-header-title" id="captchaHeaderTitle">Loading...</div>
            </div>
            <button type="button" class="captcha-close-btn" aria-label="Close verification">&times;</button>
          </div>
          <div class="captcha-body">
            <div class="captcha-grid" id="captchaGrid" role="group" aria-label="Image selection grid"></div>
            <div class="captcha-audio-panel" id="captchaAudioPanel">
              <div style="font-size:14px; color:var(--text-muted); font-weight:500;">
                Listen to the numbers and type them below:
              </div>
              <button type="button" class="captcha-audio-play-btn" id="captchaAudioPlay">
                ${ICONS.play} <span>Play Audio Challenge</span>
              </button>
              <input type="text" class="captcha-audio-input" id="captchaAudioInput" placeholder="••••" maxlength="6" inputmode="numeric" pattern="[0-9]*" autocomplete="off" aria-label="Type the numbers heard">
              <button type="button" class="captcha-switch-btn" id="captchaSwitchToImage">
                ${ICONS.image} <span>Return to image challenge</span>
              </button>
            </div>
          </div>
          <div class="captcha-footer">
            <div class="captcha-tool-buttons">
              <button type="button" class="captcha-icon-btn" id="captchaRefreshBtn" title="Get a new challenge" aria-label="Refresh challenge">
                ${ICONS.refresh}
              </button>
              <button type="button" class="captcha-icon-btn" id="captchaAudioBtn" title="Audio challenge" aria-label="Audio alternative">
                ${ICONS.headphones}
              </button>
            </div>
            <div class="captcha-status-msg" id="captchaStatusMsg" aria-live="polite"></div>
            <button type="button" class="captcha-verify-btn" id="captchaVerifyBtn">Verify</button>
          </div>
        </div>
      `;

      document.body.appendChild(this.backdrop);

      this.card = this.backdrop.querySelector('.captcha-modal-card');
      this.titleEl = this.backdrop.querySelector('#captchaHeaderTitle');
      this.gridEl = this.backdrop.querySelector('#captchaGrid');
      this.audioPanel = this.backdrop.querySelector('#captchaAudioPanel');
      this.audioPlayBtn = this.backdrop.querySelector('#captchaAudioPlay');
      this.audioInput = this.backdrop.querySelector('#captchaAudioInput');
      this.switchToImageBtn = this.backdrop.querySelector('#captchaSwitchToImage');
      this.refreshBtn = this.backdrop.querySelector('#captchaRefreshBtn');
      this.audioBtn = this.backdrop.querySelector('#captchaAudioBtn');
      this.verifyBtn = this.backdrop.querySelector('#captchaVerifyBtn');
      this.statusMsg = this.backdrop.querySelector('#captchaStatusMsg');
      this.closeBtn = this.backdrop.querySelector('.captcha-close-btn');

      // Bind events
      this.closeBtn.addEventListener('click', () => this.close());
      this.backdrop.addEventListener('click', (e) => {
        if (e.target === this.backdrop) this.close();
      });

      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && this.isOpen()) {
          this.close();
        }
      });

      this.refreshBtn.addEventListener('click', () => this.loadNewChallenge());
      this.audioBtn.addEventListener('click', () => this.toggleAudioMode());
      this.switchToImageBtn.addEventListener('click', () => this.toggleAudioMode(false));
      this.audioPlayBtn.addEventListener('click', () => this.playAudio());
      this.verifyBtn.addEventListener('click', () => this.submitVerification());

      this.audioInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          this.submitVerification();
        }
      });
    }

    isOpen() {
      return this.backdrop.classList.contains('open');
    }

    async open() {
      this.mode = 'image';
      this.selectedIndexes.clear();
      this.statusMsg.textContent = '';
      this.backdrop.classList.add('open');
      this.toggleAudioMode(false);
      await this.loadNewChallenge();
    }

    close() {
      this.backdrop.classList.remove('open');
      if (this.audioObj) {
        this.audioObj.pause();
        this.audioObj = null;
      }
      this.onClose();
    }

    toggleAudioMode(enableAudio = null) {
      if (enableAudio === null) {
        this.mode = this.mode === 'image' ? 'audio' : 'image';
      } else {
        this.mode = enableAudio ? 'audio' : 'image';
      }

      if (this.mode === 'audio') {
        this.gridEl.style.display = 'none';
        this.audioPanel.classList.add('active');
        this.titleEl.textContent = 'Audio Challenge';
        this.audioInput.value = '';
        this.verifyBtn.disabled = false;
        setTimeout(() => this.audioInput.focus(), 100);
      } else {
        this.audioPanel.classList.remove('active');
        this.gridEl.style.display = 'grid';
        if (this.challenge) {
          this.titleEl.textContent = this.challenge.category || 'Images';
        }
        this.verifyBtn.disabled = !this.imagesLoaded;
      }
    }

    async loadNewChallenge() {
      this.statusMsg.textContent = '';
      this.selectedIndexes.clear();
      this.imagesLoaded = false;
      this.verifyBtn.disabled = true;

      if (this.mode === 'image') {
        this.gridEl.innerHTML = `
          <div style="grid-column: 1/-1; display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:240px; color:var(--text-muted); gap:12px;">
            <div class="captcha-spinner" style="display:block; width:28px; height:28px; border-width:3px;"></div>
            <span style="font-size:13px; font-weight:600;">Loading challenge...</span>
          </div>
        `;
      }

      try {
        const res = await fetch('/api/captcha/challenge', {
          headers: { 'Accept': 'application/json' }
        });
        const data = await res.json();

        if (!res.ok) {
          this.statusMsg.textContent = data.error || 'Failed to load challenge.';
          return;
        }

        this.challenge = data;
        this.renderChallenge();
      } catch (err) {
        this.statusMsg.textContent = 'Network error loading challenge.';
      }
    }

    renderChallenge() {
      if (!this.challenge) return;

      if (this.mode === 'image') {
        this.titleEl.textContent = this.challenge.category || 'Images';
      }

      const is4x4 = this.challenge.gridSize === 4;
      this.gridEl.classList.toggle('grid-4x4', is4x4);
      this.gridEl.innerHTML = '';

      this.imagesLoaded = false;
      this.verifyBtn.disabled = true;

      const images = this.challenge.images || [];
      const totalImages = images.length;
      let loadedCount = 0;
      let hasError = false;

      if (totalImages === 0) {
        return;
      }

      images.forEach((imgItem, idx) => {
        const tile = document.createElement('div');
        tile.className = 'captcha-tile';
        tile.setAttribute('role', 'checkbox');
        tile.setAttribute('aria-checked', 'false');
        tile.setAttribute('tabindex', '0');
        tile.setAttribute('aria-label', `Challenge image ${idx + 1}`);

        // Skeleton placeholder while image is downloading
        const skeleton = document.createElement('div');
        skeleton.className = 'captcha-skeleton';
        skeleton.setAttribute('aria-hidden', 'true');
        tile.appendChild(skeleton);

        // Challenge Image
        const img = document.createElement('img');
        img.alt = '';
        img.loading = 'eager';
        img.draggable = false;

        img.onload = () => {
          if (!tile.classList.contains('loaded')) {
            tile.classList.add('loaded');
            loadedCount++;
            if (loadedCount >= totalImages && !hasError) {
              this.imagesLoaded = true;
              if (this.mode === 'image') {
                this.verifyBtn.disabled = false;
              }
            }
          }
        };

        img.onerror = () => {
          // Retry once with cache-busting parameter
          if (!img.dataset.retried) {
            img.dataset.retried = '1';
            const sep = imgItem.url.includes('?') ? '&' : '?';
            img.src = `${imgItem.url}${sep}_retry=1&_t=${Date.now()}`;
            return;
          }

          // If retry fails too, trigger safety recovery
          if (!hasError) {
            hasError = true;
            this.imagesLoaded = false;
            this.verifyBtn.disabled = true;
            this.statusMsg.style.color = 'var(--text-muted)';
            this.statusMsg.textContent = "Couldn't load images, loading a new challenge";
            setTimeout(() => {
              if (this.isOpen()) {
                this.loadNewChallenge();
              }
            }, 1200);
          }
        };

        img.src = imgItem.url;
        tile.appendChild(img);

        // Selection overlay
        const overlay = document.createElement('div');
        overlay.className = 'captcha-overlay';
        overlay.innerHTML = `<div class="captcha-tile-check">${ICONS.check}</div>`;
        tile.appendChild(overlay);

        const toggleTile = () => {
          if (this.selectedIndexes.has(idx)) {
            this.selectedIndexes.delete(idx);
            tile.classList.remove('selected');
            tile.setAttribute('aria-checked', 'false');
          } else {
            this.selectedIndexes.add(idx);
            tile.classList.add('selected');
            tile.setAttribute('aria-checked', 'true');
          }
          this.statusMsg.textContent = '';
        };

        tile.addEventListener('click', toggleTile);
        tile.addEventListener('keydown', (e) => {
          if (e.key === ' ' || e.key === 'Enter') {
            e.preventDefault();
            toggleTile();
          }
        });

        this.gridEl.appendChild(tile);
      });
    }

    playAudio() {
      if (!this.challenge || !this.challenge.audioUrl) return;
      if (this.audioObj) {
        this.audioObj.pause();
      }
      this.audioObj = new Audio(this.challenge.audioUrl + '?t=' + Date.now());
      this.audioPlayBtn.classList.add('playing');
      this.audioObj.play().catch(() => {
        this.statusMsg.textContent = 'Audio playback error.';
      });
      this.audioObj.onended = () => {
        this.audioPlayBtn.classList.remove('playing');
      };
    }

    async submitVerification() {
      if (!this.challenge) return;

      this.verifyBtn.disabled = true;
      this.statusMsg.style.color = 'var(--text-muted)';
      this.statusMsg.textContent = 'Verifying...';

      const payload = {
        challengeId: this.challenge.challengeId,
        website_hp: ''
      };

      if (this.mode === 'audio') {
        const val = this.audioInput.value.trim();
        if (!val) {
          this.statusMsg.style.color = '#ef4444';
          this.statusMsg.textContent = 'Please enter numbers.';
          this.verifyBtn.disabled = false;
          return;
        }
        payload.audioAnswer = val;
      } else {
        payload.selectedIndexes = Array.from(this.selectedIndexes);
      }

      try {
        const res = await fetch('/api/captcha/verify', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
          },
          body: JSON.stringify(payload)
        });

        const data = await res.json();

        if (res.ok && data.success && data.captchaToken) {
          this.statusMsg.style.color = '#10b981';
          this.statusMsg.textContent = '✓ Verified!';
          setTimeout(() => {
            this.close();
            this.onSuccess(data.captchaToken);
          }, 350);
        } else {
          // Failure: Show Try again and automatically load new challenge
          this.statusMsg.style.color = '#ef4444';
          this.statusMsg.textContent = 'Try again';
          setTimeout(() => {
            this.loadNewChallenge();
          }, 800);
        }
      } catch (err) {
        this.statusMsg.style.color = '#ef4444';
        this.statusMsg.textContent = 'Network error. Try again.';
        this.verifyBtn.disabled = false;
      }
    }
  }

  /**
   * Main useCaptcha hook & controller
   */
  function useCaptcha(options = {}) {
    const container = typeof options.container === 'string'
      ? document.querySelector(options.container)
      : options.container;

    const submitBtn = typeof options.submitBtn === 'string'
      ? document.querySelector(options.submitBtn)
      : options.submitBtn;

    let token = '';
    let verified = false;
    let provider = 'custom';
    let checkbox = null;
    let modal = null;

    // Initially disable submit button if configured
    if (submitBtn) {
      submitBtn.disabled = true;
    }

    const onVerified = (newToken) => {
      token = newToken;
      verified = true;
      if (checkbox) checkbox.setVerified(token);
      if (submitBtn) submitBtn.disabled = false;
      if (typeof options.onVerified === 'function') {
        options.onVerified(token);
      }
    };

    const onReset = (autoLoadNew = true) => {
      token = '';
      verified = false;
      if (checkbox) checkbox.reset();
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.classList.remove('loading');
      }
      if (modal && autoLoadNew) {
        modal.loadNewChallenge();
      }
      if (typeof options.onReset === 'function') {
        options.onReset();
      }
    };

    // Initialize provider and DOM
    async function init() {
      try {
        const res = await fetch('/api/captcha/config');
        const cfg = await res.json();
        provider = (cfg && cfg.provider) || 'custom';

        if (provider === 'turnstile') {
          initTurnstile(cfg.siteKey);
          return;
        } else if (provider === 'recaptcha') {
          initRecaptcha(cfg.siteKey);
          return;
        }
      } catch (e) {
        provider = 'custom';
      }

      initCustom();
    }

    function initCustom() {
      if (!modal) {
        modal = new CaptchaModal({
          onSuccess: (tok) => onVerified(tok),
          onClose: () => {
            if (!verified && checkbox) {
              checkbox.setLoading(false);
            }
          }
        });
      }

      checkbox = new CaptchaCheckbox({
        container: container,
        inputName: options.inputName || 'captchaToken',
        onClick: () => {
          if (checkbox) checkbox.setLoading(true);
          modal.open();
        }
      });
    }

    function initTurnstile(siteKey) {
      if (!container) return;
      container.innerHTML = `<div class="cf-turnstile" data-sitekey="${siteKey}"></div>`;
      if (!window.turnstile) {
        const script = document.createElement('script');
        script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js';
        script.async = true;
        document.head.appendChild(script);
      }
      window.turnstileCallback = (tok) => onVerified(tok);
    }

    function initRecaptcha(siteKey) {
      if (!container) return;
      container.innerHTML = `<div class="g-recaptcha" data-sitekey="${siteKey}" data-callback="onRecaptchaSuccess"></div>`;
      window.onRecaptchaSuccess = (tok) => onVerified(tok);
      if (!window.grecaptcha) {
        const script = document.createElement('script');
        script.src = 'https://www.google.com/recaptcha/api.js';
        script.async = true;
        document.head.appendChild(script);
      }
    }

    init();

    return {
      getToken: () => token,
      isVerified: () => verified,
      reset: onReset,
      openChallenge: () => { if (modal) modal.open(); }
    };
  }

  // Export to window
  window.CaptchaCheckbox = CaptchaCheckbox;
  window.CaptchaModal = CaptchaModal;
  window.useCaptcha = useCaptcha;

})();
