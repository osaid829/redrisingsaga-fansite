// ==========================================
// RED RISING SAGA — 3D INTERACTIVE SCRIPT (script.js)
// ==========================================

document.addEventListener('DOMContentLoaded', () => {

  // 1. NAVBAR SCROLL EFFECT & CART COUNTER
  const navbar = document.getElementById('navbar');
  window.addEventListener('scroll', () => {
    if (window.scrollY > 40) {
      navbar.classList.add('scrolled');
    } else {
      navbar.classList.remove('scrolled');
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
  let currentPlayingCard = null;
  let currentAudio = null;
  let accessTokens = {};
  const BACKEND_BASE_URL = window.location.port === '3000' ? 'http://127.0.0.1:8000' : '';
  const AUDIO_BASE_URL = BACKEND_BASE_URL;

  try {
    const savedTokens = JSON.parse(localStorage.getItem('red-rising-access-tokens') || '{}');
    if (savedTokens && typeof savedTokens === 'object') accessTokens = savedTokens;
  } catch (error) {
    accessTokens = {};
  }

  function persistAccessTokens() {
    localStorage.setItem('red-rising-access-tokens', JSON.stringify(accessTokens));
  }

  function buildAudioSrc(productId, filename) {
    const token = accessTokens[productId] || '';
    const tokenParam = token ? `?token=${encodeURIComponent(token)}` : '';
    return `${AUDIO_BASE_URL}/api/audio/${productId}/${encodeURIComponent(filename)}${tokenParam}`;
  }

  function triggerDownload(productId) {
    const token = accessTokens[productId];
    if (!token) return false;
    const downloadUrl = `${AUDIO_BASE_URL}/api/download/${encodeURIComponent(productId)}?token=${encodeURIComponent(token)}`;
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.target = '_blank';
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    link.remove();
    return true;
  }

  function resolvePreviewFile(productId) {
    const catalog = window.RED_RISING_AUDIO_CATALOG || {};
    const entry = catalog[productId] || {};
    return entry.previewFile || 'CHAPTER 01.mp3';
  }

  function hydratePreviewSources() {
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

  function updateChapterSources(productId) {
    document.querySelectorAll('.chapter-card').forEach(card => {
      const btn = card.querySelector('.chapter-play-btn');
      const audio = card.querySelector('audio');
      const source = audio ? audio.querySelector('source') : null;
      if (!btn || !audio || !source) return;
      const filename = btn.getAttribute('data-src') || resolvePreviewFile(productId);
      const btnProductId = btn.getAttribute('data-product-id') || 'red-rising-audio';
      if (btnProductId !== productId) return;
      source.src = buildAudioSrc(btnProductId, filename);
      audio.load();
    });
  }

  function showPurchasePrompt(productId, title) {
    showToast(`Preview unlocked for ${title}. Purchase to continue listening past the first 10 minutes.`);
  }

  hydratePreviewSources();

  playButtons.forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const card = btn.closest('.chapter-card');
      const playerContainer = card.querySelector('.chapter-player');
      const audio = card.querySelector('audio');
      const productId = btn.getAttribute('data-product-id') || 'red-rising-audio';
      const filename = btn.getAttribute('data-src') || resolvePreviewFile(productId);
      const source = audio.querySelector('source');
      if (source && filename) {
        source.src = buildAudioSrc(productId, filename);
        audio.load();
      }

      if (currentAudio === audio) {
        if (audio.paused) {
          audio.play().catch(() => {
            card.classList.remove('playing');
            btn.querySelector('.play-icon').textContent = '▶';
            playerContainer.classList.remove('open');
            showPurchasePrompt(productId, 'this audiobook');
          });
          card.classList.add('playing');
          btn.querySelector('.play-icon').textContent = '❚❚';
        } else {
          audio.pause();
          card.classList.remove('playing');
          btn.querySelector('.play-icon').textContent = '▶';
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
      btn.querySelector('.play-icon').textContent = '❚❚';
      audio.play().catch(() => {
        card.classList.remove('playing');
        btn.querySelector('.play-icon').textContent = '▶';
        playerContainer.classList.remove('open');
        showPurchasePrompt(productId, 'this audiobook');
      });

      currentAudio = audio;
      currentPlayingCard = card;

      audio.onended = () => {
        card.classList.remove('playing');
        btn.querySelector('.play-icon').textContent = '▶';
        playerContainer.classList.remove('open');
        if (!accessTokens[productId]) {
          showToast('Preview ended. Purchase this audiobook to unlock the full recording.');
        }
        currentAudio = null;
        currentPlayingCard = null;
      };
    });
  });

  // Card click to play toggle
  document.querySelectorAll('.chapter-card').forEach(card => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('.chapter-play-btn') || e.target.closest('.chapter-player')) return;
      const btn = card.querySelector('.chapter-play-btn');
      if (btn) btn.click();
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
  let cartCount = 0;
  const cartBadge = document.getElementById('cart-count');
  
  function showToast(message) {
    let container = document.querySelector('.toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'toast-container';
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

  async function api(path, payload) {
    const response = await fetch(`${BACKEND_BASE_URL}${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload), credentials: 'same-origin'
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || 'Something went wrong. Please try again.');
    return data;
  }

  async function initiateRazorpayPayment(productId, button) {
    button.disabled = true;
    const originalLabel = button.textContent;
    button.textContent = 'Preparing secure checkout…';
    try {
      const currencySelect = document.getElementById('checkout-currency');
      const currency = currencySelect ? currencySelect.value : 'INR';
      const order = await api('/api/order', { product_id: productId, currency });
      if (order.simulation) {
        const accessResponse = await api('/api/access/grant', { product_id: productId });
        accessTokens[productId] = accessResponse.access_token;
        persistAccessTokens();
        updateChapterSources(productId);
        cartCount += 1;
        if (cartBadge) cartBadge.textContent = cartCount;
        if (triggerDownload(productId)) {
          showToast(`Demo checkout ready for ${order.name} — your download has started.`);
        } else {
          showToast(`Demo checkout ready for ${order.name} — full access unlocked.`);
        }
        return;
      }
      if (typeof Razorpay === 'undefined') throw new Error('Secure checkout failed to load. Please refresh and try again.');
      const configResponse = await fetch(`${BACKEND_BASE_URL}/api/config`, { credentials: 'same-origin' });
      const config = await configResponse.json();
      if (!configResponse.ok || !config.payments_enabled || !config.razorpay_key_id) throw new Error('Payments are not configured yet.');
      const checkout = new Razorpay({
        key: config.razorpay_key_id,
        amount: order.amount, currency: order.currency, name: 'Red Rising Saga Armory',
        description: order.name, image: 'hero-bg.png', order_id: order.order_id,
        theme: { color: '#b91c1c' }, modal: { ondismiss: () => showToast('Checkout cancelled — nothing was charged.') },
        handler: async response => {
          try {
            const result = await api('/api/verify', response);
            accessTokens[productId] = result.access_token;
            persistAccessTokens();
            updateChapterSources(productId);
            cartCount += 1;
            if (cartBadge) cartBadge.textContent = cartCount;
            if (triggerDownload(productId)) {
              showToast('Payment verified securely. Your download has started.');
            } else {
              showToast('Payment verified securely. Thank you, Howler.');
            }
          } catch (error) { showToast(error.message); }
        }
      });
      checkout.open();
    } catch (error) {
      showToast(error.message || 'Checkout could not be started.');
    } finally {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  }

  document.querySelectorAll('.buy-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const productId = btn.getAttribute('data-product-id');
      if (productId) initiateRazorpayPayment(productId, btn);
    });
  });

  // 7. 3D CHARACTER MODAL SYSTEM
  const modalOverlay = document.getElementById('modal-overlay');
  const modalBox = document.getElementById('modal-box');

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
