// ==========================================
// RED RISING SAGA — 3D INTERACTIVE SCRIPT (script.js)
// ==========================================

document.addEventListener('DOMContentLoaded', () => {

  // 1. NAVBAR SCROLL EFFECT & CART COUNTER
  const navbar = document.getElementById('navbar');
  window.addEventListener('scroll', () => {
    if (window.scrollY > 40) {
      navbar?.classList.add('scrolled');
    } else {
      navbar?.classList.remove('scrolled');
    }
  });

  // 2. EMBERS PARTICLE GENERATOR (3D Motion)
  const particlesContainer = document.getElementById('hero-particles');
  if (particlesContainer) {
    const emberCount = 35;
    for (let i = 0; i < emberCount; i++) {
      const ember = document.createElement('div');
      ember.className = 'ember';
      
      const left = Math.random() * 100;
      const duration = 6 + Math.random() * 9;
      const delay = Math.random() * 7;
      const size = 2 + Math.random() * 4;
      const glow = Math.random() > 0.5 ? '#c9a84c' : '#ef4444';
      
      ember.style.left = `${left}%`;
      ember.style.bottom = `-20px`;
      ember.style.width = `${size}px`;
      ember.style.height = `${size}px`;
      ember.style.background = glow;
      ember.style.boxShadow = `0 0 10px ${glow}`;
      ember.style.animationDuration = `${duration}s`;
      ember.style.animationDelay = `${delay}s`;
      
      particlesContainer.appendChild(ember);
    }
  }

  // 3. REAL-TIME 3D CARD TILT EFFECT ON MOUSEMOVE
  const tiltCards = document.querySelectorAll('.tilt-card');
  tiltCards.forEach(card => {
    if (!(card instanceof HTMLElement)) return;
    card.addEventListener('mousemove', (e) => {
      const rect = card.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      
      const centerX = rect.width / 2;
      const centerY = rect.height / 2;
      
      const rotateX = ((y - centerY) / centerY) * -12; // tilt angle X
      const rotateY = ((x - centerX) / centerX) * 12;  // tilt angle Y
      
      card.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale3d(1.02, 1.02, 1.02)`;
    });

    card.addEventListener('mouseleave', () => {
      card.style.transform = `perspective(1000px) rotateX(0deg) rotateY(0deg) scale3d(1, 1, 1)`;
    });
  });

  // 4. AUDIOBOOK CHAPTER PLAYER LOGIC
  const playButtons = document.querySelectorAll('.chapter-play-btn');
  /** @type {Element | null} */
  let currentPlayingCard = null;
  /** @type {HTMLAudioElement | null} */
  let currentAudio = null;
  const BACKEND_BASE_URL = '';
  const AUDIO_BASE_URL = BACKEND_BASE_URL;
  // Public hosts run as a fan edition: no checkout and no distribution of media.
  const publicFanEdition = !['localhost', '127.0.0.1'].includes(window.location.hostname);
  const officialSagaUrl = 'https://www.piercebrown.com/redrisingsaga';
  /** @type {Set<string>} */
  const ownedProducts = new Set();
  /** @type {Map<string, PaymentProof>} */
  const pendingVerifications = new Map();
  let checkoutActive = false;
  try {
    localStorage.removeItem('red-rising-access-tokens');
    for (const proof of JSON.parse(sessionStorage.getItem('rr-pending-payments') || '[]')) {
      if (proof && typeof proof.product_id === 'string' && typeof proof.razorpay_order_id === 'string'
          && typeof proof.razorpay_payment_id === 'string' && typeof proof.razorpay_signature === 'string') {
        pendingVerifications.set(proof.product_id, proof);
      }
    }
  } catch (_) { /* Storage is optional; purchases still use the server session. */ }

  function savePendingVerifications() {
    try { sessionStorage.setItem('rr-pending-payments', JSON.stringify([...pendingVerifications.values()])); } catch (_) {}
  }

  async function refreshPurchases() {
    const response = await fetch('/api/purchases', { credentials: 'same-origin' });
    if (!response.ok) return;
    const data = await response.json();
    if (!Array.isArray(data.products)) return;
    ownedProducts.clear();
    data.products.forEach((/** @type {unknown} */ product) => { if (typeof product === 'string') ownedProducts.add(product); });
    updatePurchaseLabels();
  }

  function updatePurchaseLabels() {
    document.querySelectorAll('.buy-btn').forEach(button => {
      const product = button.getAttribute('data-product-id') || '';
      if (ownedProducts.has(product)) button.textContent = 'Download purchase';
      else if (pendingVerifications.has(product)) button.textContent = 'Retry payment verification';
    });
  }

  /** @param {string} productId @param {string} filename */
  function buildAudioSrc(productId, filename) {
    return `${AUDIO_BASE_URL}/api/audio/${productId}/${encodeURIComponent(filename)}`;
  }

  /** @param {string} productId */
  async function triggerDownload(productId) {
    const ready = await fetch(`/api/download-ready/${encodeURIComponent(productId)}`, { credentials: 'same-origin' });
    const result = await ready.json();
    if (!ready.ok) throw new Error(result.message || 'Download unavailable. Your purchase is saved.');
    const downloadUrl = `${AUDIO_BASE_URL}/api/download/${encodeURIComponent(productId)}`;
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.download = '';
    document.body.appendChild(link);
    link.click();
    link.remove();
    return true;
  }

  /** @param {string} productId */
  function resolvePreviewFile(productId) {
    const catalog = window.RED_RISING_AUDIO_CATALOG || {};
    const entry = catalog[productId] || {};
    return entry.previewFile || 'CHAPTER 01.mp3';
  }

  function hydratePreviewSources() {
    if (publicFanEdition) return;
    document.querySelectorAll('.chapter-card').forEach(card => {
      const btn = card.querySelector('.chapter-play-btn');
      const audio = card.querySelector('audio');
      const source = audio ? audio.querySelector('source') : null;
      if (!btn || !audio || !source) return;
      const productId = btn.getAttribute('data-product-id') || 'red-rising-audio';
      const filename = btn.getAttribute('data-src') || resolvePreviewFile(productId);
      if (filename) {
        source.src = buildAudioSrc(productId, filename);
        audio.load();
      }
    });
  }

  /** @param {string} productId */
  function updateChapterSources(productId) {
    document.querySelectorAll('.chapter-card').forEach(card => {
      const btn = card.querySelector('.chapter-play-btn');
      const audio = card.querySelector('audio');
      const source = audio ? audio.querySelector('source') : null;
      if (!btn || !audio || !source) return;
      const filename = btn.getAttribute('data-src') || resolvePreviewFile(productId);
      const btnProductId = btn.getAttribute('data-product-id') || 'red-rising-audio';
      if (btnProductId !== productId && productId !== 'saga-combo') return;
      source.src = buildAudioSrc(btnProductId, filename);
      audio.load();
    });
  }

  /** @param {string} productId @param {string} title */
  function showPurchasePrompt(productId, title) {
    showToast(`Preview unlocked for ${title}. Purchase to continue listening past the first 10 minutes.`);
  }

  hydratePreviewSources();

  playButtons.forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (publicFanEdition) {
        showToast('Audio previews are not hosted by this fan project. Find official editions below.');
        return;
      }
      const card = btn.closest('.chapter-card');
      if (!card) return;
      const playerContainer = card.querySelector('.chapter-player');
      const audio = card.querySelector('audio');
      const playIcon = btn.querySelector('.play-icon');
      if (!playerContainer || !audio || !playIcon) return;
      const productId = btn.getAttribute('data-product-id') || 'red-rising-audio';
      const filename = btn.getAttribute('data-src') || resolvePreviewFile(productId);
      const source = audio.querySelector('source');
      const audioSrc = buildAudioSrc(productId, filename);
      if (source && filename && source.getAttribute('src') !== audioSrc) {
        source.src = audioSrc;
        audio.load();
      }

      if (currentAudio === audio) {
        if (audio.paused) {
          audio.play().catch(() => {
            card.classList.remove('playing');
            playIcon.textContent = '▶';
            playerContainer.classList.remove('open');
            showPurchasePrompt(productId, 'this audiobook');
          });
          card.classList.add('playing');
          playIcon.textContent = '❚❚';
        } else {
          audio.pause();
          card.classList.remove('playing');
          playIcon.textContent = '▶';
        }
        return;
      }

      if (currentAudio) {
        currentAudio.pause();
        if (currentPlayingCard) {
          currentPlayingCard.classList.remove('playing');
          const prevBtn = currentPlayingCard.querySelector('.chapter-play-btn .play-icon');
          if (prevBtn) prevBtn.textContent = '▶';
          const prevPlayer = currentPlayingCard.querySelector('.chapter-player');
          if (prevPlayer) prevPlayer.classList.remove('open');
        }
      }

      playerContainer.classList.add('open');
      card.classList.add('playing');
      playIcon.textContent = '❚❚';
      audio.play().catch(() => {
        card.classList.remove('playing');
        playIcon.textContent = '▶';
        playerContainer.classList.remove('open');
        showPurchasePrompt(productId, 'this audiobook');
      });

      currentAudio = audio;
      currentPlayingCard = card;

      audio.onended = () => {
        card.classList.remove('playing');
        playIcon.textContent = '▶';
        playerContainer.classList.remove('open');
        if (!ownedProducts.has(productId)) showToast('Preview ended. Purchase this audiobook to unlock the full recording.');
        currentAudio = null;
        currentPlayingCard = null;
      };
    });
  });

  // Card click to play toggle
  document.querySelectorAll('.chapter-card').forEach(card => {
    card.addEventListener('click', (e) => {
      if (!(e.target instanceof Element)) return;
      if (e.target.closest('.chapter-play-btn') || e.target.closest('.chapter-player')) return;
      const btn = card.querySelector('.chapter-play-btn');
      if (btn instanceof HTMLElement) btn.click();
    });
  });

  // 5. STORE CATEGORY TABS FILTER
  const tabBtns = document.querySelectorAll('.store-tab-btn');
  const bookCards = document.querySelectorAll('.book-item-card');

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      tabBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      const filter = btn.getAttribute('data-filter');
      bookCards.forEach(card => {
        if (!(card instanceof HTMLElement)) return;
        const cat = card.getAttribute('data-category');
        if (filter === 'all' || cat === filter || (filter === 'combo' && cat === 'combo')) {
          card.style.display = 'flex';
          card.classList.add('animate-on-scroll', 'visible');
        } else {
          card.style.display = 'none';
        }
      });
    });
  });

  // 6. RAZORPAY PAYMENT TRANSACTION HANDLER & TOAST SYSTEM
  /** @param {string} message */
  function showToast(message) {
    let container = document.querySelector('.toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'toast-container';
      container.setAttribute('role', 'status');
      container.setAttribute('aria-live', 'polite');
      document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = 'toast';
    const icon = document.createElement('span');
    icon.textContent = '⚔';
    toast.append(icon, document.createTextNode(` ${message}`));
    container.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(100%)';
      setTimeout(() => toast.remove(), 400);
    }, 3500);
  }

  class ApiError extends Error {
    /** @param {string} message @param {number} status */
    constructor(message, status) { super(message); this.status = status; }
  }

  /** @param {string} path @param {unknown} payload */
  async function api(path, payload) {
    const response = await fetch(`${BACKEND_BASE_URL}${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload), credentials: 'same-origin'
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new ApiError(data.message || 'Something went wrong. Please try again.', response.status);
    }
    return data;
  }

  /** @param {PaymentProof} proof @param {HTMLButtonElement} button */
  async function completePurchase(proof, button) {
    pendingVerifications.set(proof.product_id, proof);
    savePendingVerifications();
    let result;
    for (let attempt = 0; attempt < 8; attempt++) {
      try {
        result = await api('/api/verify-payment', proof);
        break;
      } catch (error) {
        if (!(error instanceof ApiError) || ![409, 502, 503].includes(error.status) || attempt === 7) throw error;
        button.textContent = 'Confirming payment…';
        await new Promise(resolve => setTimeout(resolve, 2000));
      }
    }
    // Verification has already set the server session. A failed library refresh
    // must not make this buyer pay again or hide their download button.
    ownedProducts.add(result.product_id);
    pendingVerifications.delete(proof.product_id);
    savePendingVerifications();
    updatePurchaseLabels();
    await refreshPurchases().catch(() => {});
    updateChapterSources(result.product_id);
    await triggerDownload(result.product_id);
    showToast('Payment confirmed. Download requested; use Download purchase to download again.');
  }

  /** @param {string} productId @param {HTMLButtonElement} button */
  async function initiateRazorpayPayment(productId, button) {
    if (publicFanEdition) {
      window.location.assign(officialSagaUrl);
      return;
    }
    if (checkoutActive) { showToast('Finish the current checkout before starting another.'); return; }
    checkoutActive = true;
    button.disabled = true;
    const originalLabel = button.textContent;
    button.textContent = 'Preparing secure checkout…';
    try {
      if (ownedProducts.has(productId)) {
        await triggerDownload(productId);
        button.disabled = false;
        button.textContent = 'Download purchase';
        checkoutActive = false;
        return;
      }
      const pending = pendingVerifications.get(productId);
      if (pending) {
        await completePurchase(pending, button);
        button.disabled = false;
        button.textContent = 'Download purchase';
        checkoutActive = false;
        return;
      }
      const currencySelect = document.getElementById('checkout-currency');
      const currency = currencySelect instanceof HTMLSelectElement ? currencySelect.value : 'INR';
      if (typeof Razorpay === 'undefined') {
        throw new Error('Razorpay Checkout SDK failed to load. Please check your connection and reload.');
      }
      const configResponse = await fetch(`${BACKEND_BASE_URL}/api/config`, { credentials: 'same-origin' });
      const config = await configResponse.json();
      if (!configResponse.ok || !config.payments_enabled || !config.razorpay_key_id) {
        throw new Error('Payments are not configured yet.');
      }
      const order = await api('/api/create-order', { product_id: productId, currency });

      button.textContent = 'Awaiting payment…';

      const checkout = new Razorpay({
        key: config.razorpay_key_id,
        amount: order.amount,
        currency: order.currency,
        name: 'Red Rising Saga Armory',
        description: order.name,
        image: 'hero-bg.png',
        order_id: order.order_id,
        theme: { color: '#b91c1c' },
        modal: {
          ondismiss: () => {
            showToast('Checkout closed. If a payment is pending, wait for confirmation before retrying.');
            button.disabled = false;
            button.textContent = originalLabel;
            checkoutActive = false;
          }
        },
        handler: async response => {
          button.textContent = 'Verifying payment…';
          try {
            await completePurchase({
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_order_id: response.razorpay_order_id,
              razorpay_signature: response.razorpay_signature,
              product_id: productId
            }, button);
          } catch (error) {
            showToast(error instanceof Error ? error.message : 'Payment verification failed.');
          } finally {
            button.disabled = false;
            checkoutActive = false;
            button.textContent = ownedProducts.has(productId) ? 'Download purchase' : 'Retry payment verification';
          }
        }
      });

      checkout.on('payment.failed', function() {
        showToast('Payment failed or was declined.');
      });

      checkout.open();
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Checkout could not be started.');
      checkoutActive = false;
      button.disabled = false;
      button.textContent = ownedProducts.has(productId) ? 'Download purchase' : pendingVerifications.has(productId) ? 'Retry payment verification' : originalLabel;
    }
  }

  document.querySelectorAll('.buy-btn').forEach(btn => {
    if (!(btn instanceof HTMLButtonElement)) return;
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const productId = btn.getAttribute('data-product-id');
      if (productId) initiateRazorpayPayment(productId, btn);
    });
  });
  if (publicFanEdition) {
    document.querySelectorAll('.buy-btn').forEach(button => { button.textContent = 'Find official edition'; });
    document.querySelectorAll('.book-pricing-box .price-tags').forEach(element => { element.textContent = 'Official edition'; });
  } else {
    updatePurchaseLabels();
    refreshPurchases().catch(() => {});
  }

  // 7. 3D CHARACTER MODAL SYSTEM
  const modalOverlay = document.getElementById('modal-overlay');
  const modalBox = document.getElementById('modal-box');

  /** @param {Element} card */
  function renderCharacterModal(card) {
    if (!modalBox) return;

    const name = card.getAttribute('data-name') || '';
    const role = card.getAttribute('data-role') || '';
    const bio = card.getAttribute('data-bio') || '';
    const quote = card.getAttribute('data-quote') || '';
    const imgEl = card.querySelector('.char-img');
    const imgSrc = imgEl ? imgEl.getAttribute('src') || '' : '';

    modalBox.replaceChildren();

    const closeBtn = document.createElement('button');
    closeBtn.className = 'modal-close';
    closeBtn.id = 'modal-close';
    closeBtn.type = 'button';
    closeBtn.textContent = '✕';
    closeBtn.addEventListener('click', () => {
      modalOverlay?.classList.remove('open');
    });

    const contentWrap = document.createElement('div');
    contentWrap.style.cssText = 'display: flex; gap: 24px; flex-wrap: wrap; align-items: center;';

    const image = document.createElement('img');
    image.src = imgSrc;
    image.alt = `${name} portrait`;
    image.style.cssText = 'width: 140px; height: 180px; object-fit: cover; border-radius: 16px; border: 1px solid var(--clr-gold); box-shadow: var(--shadow-gold);';

    const textWrap = document.createElement('div');
    textWrap.style.cssText = 'flex: 1; min-width: 220px;';

    const roleLabel = document.createElement('p');
    roleLabel.style.cssText = 'font-size: 0.75rem; font-weight: 700; color: var(--clr-gold); letter-spacing: 0.15em; text-transform: uppercase;';
    roleLabel.textContent = role;

    const heading = document.createElement('h3');
    heading.style.cssText = 'font-family: var(--font-display); font-size: 1.8rem; color: var(--clr-cream); margin-bottom: 8px;';
    heading.textContent = name;

    const bioText = document.createElement('p');
    bioText.style.cssText = 'font-size: 0.88rem; color: rgba(247,235,211,0.6); line-height: 1.6; margin-bottom: 14px;';
    bioText.textContent = bio;

    const quoteBlock = document.createElement('blockquote');
    quoteBlock.style.cssText = 'font-style: italic; color: var(--clr-gold-lt); font-size: 0.9rem; border-left: 2px solid var(--clr-red-lt); padding-left: 12px;';
    quoteBlock.textContent = `"${quote}"`;

    textWrap.append(roleLabel, heading, bioText, quoteBlock);
    contentWrap.append(image, textWrap);
    modalBox.append(closeBtn, contentWrap);
    modalOverlay?.classList.add('open');
  }

  document.querySelectorAll('.char-card').forEach(card => {
    card.addEventListener('click', () => {
      renderCharacterModal(card);
    });
  });

  if (modalOverlay) {
    modalOverlay.addEventListener('click', (e) => {
      if (e.target === modalOverlay) modalOverlay.classList.remove('open');
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') modalOverlay.classList.remove('open');
    });
  }

  // 8. SCROLL REVEAL OBSERVER
  const observerOptions = { threshold: 0.12 };
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
      }
    });
  }, observerOptions);

  document.querySelectorAll('.char-card, .book-item-card, .chapter-card, .ebook-card, .hierarchy-wrap').forEach(el => {
    el.classList.add('animate-on-scroll');
    observer.observe(el);
  });

});
