/* ЭТРА — главная: hero-пузырьки, манифест, горизонтальная лента, марки, featured */
(function () {
  'use strict';
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- пузырьки живой ферментации в hero ---------- */
  const canvas = document.getElementById('bubbles');
  if (canvas && !reduceMotion) {
    const ctx = canvas.getContext('2d');
    let w, h, bubbles = [];
    function resize() {
      w = canvas.width = canvas.offsetWidth * devicePixelRatio;
      h = canvas.height = canvas.offsetHeight * devicePixelRatio;
    }
    resize();
    window.addEventListener('resize', resize);
    function spawn() {
      return {
        x: Math.random() * w,
        y: h + 10,
        r: (Math.random() * 2.2 + .6) * devicePixelRatio,
        s: (Math.random() * .55 + .25) * devicePixelRatio,
        drift: (Math.random() - .5) * .4,
        o: Math.random() * .35 + .12
      };
    }
    for (let i = 0; i < 36; i++) {
      const b = spawn();
      b.y = Math.random() * h;
      bubbles.push(b);
    }
    (function tick() {
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = '#EFE7D8';
      bubbles.forEach((b, i) => {
        b.y -= b.s;
        b.x += b.drift;
        if (b.y < -12) bubbles[i] = spawn();
        ctx.globalAlpha = b.o;
        ctx.beginPath();
        ctx.arc(b.x, b.y, b.r, 0, Math.PI * 2);
        ctx.fill();
      });
      ctx.globalAlpha = 1;
      requestAnimationFrame(tick);
    })();
  }

  /* hero-бутылка: лёгкий зум при скролле */
  if (!reduceMotion) {
    gsap.to('#heroBottle img', {
      yPercent: 10, scale: 1.04, ease: 'none',
      scrollTrigger: { trigger: '#hero', start: 'top top', end: 'bottom top', scrub: true }
    });
    gsap.to('.scroll-hint', {
      opacity: 0, ease: 'none',
      scrollTrigger: { trigger: '#hero', start: 'top top', end: '30% top', scrub: true }
    });
  }

  /* ---------- манифест: подсветка слов по скроллу ---------- */
  const quote = document.getElementById('manifestoQuote');
  if (quote) {
    const words = quote.textContent.trim().split(/\s+/);
    quote.innerHTML = words.map(wd => `<span class="w">${wd}</span>`).join(' ');
    const spans = quote.querySelectorAll('.w');
    ScrollTrigger.create({
      trigger: '.manifesto',
      start: 'top 70%',
      end: 'center center',
      scrub: true,
      onUpdate: self => {
        const lit = Math.floor(self.progress * spans.length);
        spans.forEach((s, i) => s.classList.toggle('is-lit', i <= lit));
      }
    });
  }

  /* ---------- горизонтальная лента категорий ---------- */
  const track = document.getElementById('catTrack');
  if (track && !reduceMotion && window.innerWidth > 900) {
    const pin = document.querySelector('.catstrip-pin');
    const getDist = () => track.scrollWidth - window.innerWidth;
    gsap.to(track, {
      x: () => -getDist(),
      ease: 'none',
      scrollTrigger: {
        trigger: pin,
        start: 'top 12%',
        end: () => '+=' + getDist(),
        pin: true,
        scrub: 1,
        invalidateOnRefresh: true,
        anticipatePin: 1
      }
    });
  } else if (track) {
    track.parentElement.style.overflowX = 'auto';
    track.style.paddingBottom = '12px';
  }

  /* ---------- лента отзывов: автодрейф ---------- */
  const reviews = document.getElementById('reviewTrack');
  if (reviews && !reduceMotion) {
    gsap.to(reviews, {
      x: () => -(reviews.scrollWidth - window.innerWidth + 40),
      ease: 'none',
      scrollTrigger: { trigger: '.reviews', start: 'top bottom', end: 'bottom top', scrub: 1.2 }
    });
  } else if (reviews) {
    reviews.parentElement.style.overflowX = 'auto';
  }

  /* ---------- марки партнёров ---------- */
  const marquee = document.querySelector('#marquee .marquee-inner');
  if (marquee) {
    marquee.parentElement.appendChild(marquee.cloneNode(true));
    if (!reduceMotion) {
      gsap.to('#marquee .marquee-inner', { xPercent: -100, repeat: -1, duration: 36, ease: 'none' });
    }
  }

  /* ---------- featured продукты ---------- */
  const grid = document.getElementById('featuredGrid');
  if (grid && window.ETRA_PRODUCTS) {
    const ids = ['oblepiha', 'energetik', 'tarhun', 'hmel'];
    grid.innerHTML = ids
      .map(id => ETRA_PRODUCTS.find(p => p.id === id))
      .filter(Boolean)
      .map(p => ETRA_CARD(p))
      .join('');
    grid.querySelectorAll('[data-reveal]').forEach(el => {
      ScrollTrigger.create({ trigger: el, start: 'top 90%', once: true, onEnter: () => el.classList.add('is-in') });
    });
  }

  setTimeout(() => ScrollTrigger.refresh(), 300);
})();
