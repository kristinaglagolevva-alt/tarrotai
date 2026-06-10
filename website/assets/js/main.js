/* ЭТРА — общий слой: прелоадер, smooth scroll, курсор, reveal, корзина, виджеты */
(function () {
  'use strict';

  gsap.registerPlugin(ScrollTrigger);
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- Lenis smooth scroll ---------- */
  let lenis = null;
  if (!reduceMotion && typeof Lenis !== 'undefined') {
    lenis = new Lenis({ duration: 1.25, easing: t => Math.min(1, 1.001 - Math.pow(2, -10 * t)) });
    lenis.on('scroll', ScrollTrigger.update);
    gsap.ticker.add(time => lenis.raf(time * 1000));
    gsap.ticker.lagSmoothing(0);
    document.documentElement.classList.add('lenis');
  }
  window.ETRA_LENIS = lenis;

  /* якорные ссылки через lenis */
  document.querySelectorAll('a[href^="#"]').forEach(a => {
    a.addEventListener('click', e => {
      const id = a.getAttribute('href');
      if (id.length < 2) return;
      const target = document.querySelector(id);
      if (!target) return;
      e.preventDefault();
      document.body.classList.remove('menu-open');
      document.getElementById('mobileMenu')?.classList.remove('is-open');
      if (lenis) lenis.scrollTo(target, { offset: -70 });
      else target.scrollIntoView({ behavior: 'smooth' });
    });
  });

  /* ---------- прелоадер ---------- */
  const preloader = document.getElementById('preloader');
  function introReveal() {
    const heroEls = document.querySelectorAll('#hero [data-reveal], #hero [data-reveal-line], .page-hero [data-reveal], .page-hero [data-reveal-line]');
    heroEls.forEach((el, i) => {
      setTimeout(() => activateReveal(el), 90 * i);
    });
  }
  if (preloader && !sessionStorage.getItem('etra_seen')) {
    const tl = gsap.timeline({
      onComplete: () => {
        preloader.style.display = 'none';
        sessionStorage.setItem('etra_seen', '1');
        introReveal();
      }
    });
    tl.to('.preloader-logo span', { y: 0, duration: .9, stagger: .08, ease: 'power4.out', delay: .15 })
      .to('.preloader-sub', { opacity: 1, duration: .6 }, '-=.4')
      .to('.preloader-line i', { scaleX: 1, duration: .9, ease: 'power2.inOut' }, '-=.5')
      .to(preloader, { yPercent: -100, duration: .9, ease: 'power4.inOut', delay: .2 });
  } else if (preloader) {
    preloader.style.display = 'none';
    setTimeout(introReveal, 60);
  }

  /* ---------- reveal-анимации ---------- */
  function activateReveal(el) {
    if (el.hasAttribute('data-reveal')) {
      const d = parseFloat(el.getAttribute('data-reveal-delay') || 0);
      setTimeout(() => el.classList.add('is-in'), d * 1000);
    }
    if (el.hasAttribute('data-reveal-line')) {
      gsap.to(el.querySelectorAll('.rl-inner'), { y: 0, duration: 1.15, stagger: .12, ease: 'power4.out' });
    }
  }
  document.querySelectorAll('[data-reveal], [data-reveal-line]').forEach(el => {
    ScrollTrigger.create({
      trigger: el,
      start: 'top 88%',
      once: true,
      onEnter: () => activateReveal(el)
    });
  });

  /* параллакс изображений */
  if (!reduceMotion) {
    document.querySelectorAll('[data-parallax-img] img').forEach(img => {
      gsap.to(img, {
        yPercent: 12,
        ease: 'none',
        scrollTrigger: { trigger: img.closest('[data-parallax-img]'), start: 'top bottom', end: 'bottom top', scrub: true }
      });
    });
  }

  /* счётчики */
  document.querySelectorAll('[data-counter]').forEach(el => {
    const target = parseInt(el.getAttribute('data-counter'), 10);
    ScrollTrigger.create({
      trigger: el, start: 'top 90%', once: true,
      onEnter: () => {
        const obj = { v: 0 };
        gsap.to(obj, {
          v: target, duration: 1.8, ease: 'power2.out',
          onUpdate: () => { el.textContent = Math.round(obj.v).toLocaleString('ru-RU') + (target >= 1000 ? '+' : ''); }
        });
      }
    });
  });

  /* бары результатов */
  document.querySelectorAll('.bar-row').forEach(row => {
    ScrollTrigger.create({ trigger: row, start: 'top 88%', once: true, onEnter: () => row.classList.add('is-in') });
  });

  /* смена темы тела по тёмным секциям */
  document.querySelectorAll('[data-theme="dark"]').forEach(sec => {
    ScrollTrigger.create({
      trigger: sec,
      start: 'top 55%',
      end: 'bottom 45%',
      onEnter: () => document.body.classList.add('theme-dark'),
      onLeave: () => document.body.classList.remove('theme-dark'),
      onEnterBack: () => document.body.classList.add('theme-dark'),
      onLeaveBack: () => document.body.classList.remove('theme-dark')
    });
  });

  /* ---------- шапка ---------- */
  const header = document.getElementById('header');
  const announceEl = document.getElementById('announce');
  let lastY = 0;
  function headerOffset() {
    const h = (announceEl && !announceEl.classList.contains('is-hidden')) ? announceEl.offsetHeight : 0;
    header.style.top = Math.max(0, h - window.scrollY) + 'px';
  }
  function onScroll() {
    const y = window.scrollY;
    headerOffset();
    header.classList.toggle('is-scrolled', y > 30);
    if (y > 500 && y > lastY + 6) header.classList.add('is-hidden');
    else if (y < lastY - 6 || y < 200) header.classList.remove('is-hidden');
    lastY = y;
  }
  if (lenis) lenis.on('scroll', onScroll); else window.addEventListener('scroll', onScroll, { passive: true });

  /* бургер */
  const burger = document.getElementById('burger');
  const mobileMenu = document.getElementById('mobileMenu');
  burger?.addEventListener('click', () => {
    const open = document.body.classList.toggle('menu-open');
    mobileMenu?.classList.toggle('is-open', open);
    if (lenis) open ? lenis.stop() : lenis.start();
  });

  /* анонс */
  const announce = document.getElementById('announce');
  if (announce) {
    if (sessionStorage.getItem('etra_announce_closed')) announce.classList.add('is-hidden');
    announce.querySelector('.announce-close')?.addEventListener('click', () => {
      announce.classList.add('is-hidden');
      sessionStorage.setItem('etra_announce_closed', '1');
      headerOffset();
    });
  }
  headerOffset();
  window.addEventListener('resize', headerOffset);

  /* ---------- курсор ---------- */
  const dot = document.querySelector('.cursor-dot');
  const ring = document.querySelector('.cursor-ring');
  if (dot && ring && window.matchMedia('(hover: hover)').matches) {
    let mx = -100, my = -100, rx = -100, ry = -100;
    window.addEventListener('mousemove', e => { mx = e.clientX; my = e.clientY; });
    gsap.ticker.add(() => {
      rx += (mx - rx) * .14;
      ry += (my - ry) * .14;
      dot.style.transform = `translate(${mx}px, ${my}px) translate(-50%,-50%)`;
      ring.style.transform = `translate(${rx}px, ${ry}px) translate(-50%,-50%)`;
    });
    document.querySelectorAll('a, button, [data-cursor]').forEach(el => {
      el.addEventListener('mouseenter', () => ring.classList.add('is-active'));
      el.addEventListener('mouseleave', () => ring.classList.remove('is-active'));
    });
  }

  /* ---------- аккордеоны (что внутри + faq) ---------- */
  function bindAccordion(rootSel, itemSel, headSel, bodySel, single) {
    document.querySelectorAll(rootSel).forEach(root => {
      root.querySelectorAll(itemSel).forEach(item => {
        const head = item.querySelector(headSel);
        const body = item.querySelector(bodySel);
        if (!head || !body) return;
        if (item.classList.contains('is-open')) body.style.maxHeight = body.scrollHeight + 'px';
        head.addEventListener('click', () => {
          const isOpen = item.classList.contains('is-open');
          if (single) {
            root.querySelectorAll(itemSel + '.is-open').forEach(o => {
              o.classList.remove('is-open');
              o.querySelector(bodySel).style.maxHeight = '0px';
            });
          }
          if (!isOpen) {
            item.classList.add('is-open');
            body.style.maxHeight = body.scrollHeight + 'px';
            const img = item.getAttribute('data-img');
            const target = document.getElementById('insideImg');
            if (img && target && target.src.indexOf(img) === -1) {
              gsap.to(target, { opacity: 0, duration: .3, onComplete: () => {
                target.src = img;
                gsap.to(target, { opacity: 1, duration: .5 });
              }});
            }
          } else if (!single) {
            item.classList.remove('is-open');
            body.style.maxHeight = '0px';
          }
          setTimeout(() => ScrollTrigger.refresh(), 720);
        });
      });
    });
  }
  bindAccordion('#insideAcc', '.acc-item', '.acc-head', '.acc-body', true);
  bindAccordion('.faq-wrap', '.faq-item', '.faq-q', '.faq-a', true);
  document.querySelectorAll('.faq-item.is-open .faq-a').forEach(b => b.style.maxHeight = b.scrollHeight + 'px');

  /* ---------- виджет поддержки ---------- */
  const fab = document.getElementById('supportFab');
  const pop = document.getElementById('supportPop');
  fab?.addEventListener('click', () => pop.classList.toggle('is-open'));
  document.addEventListener('click', e => {
    if (pop && !pop.contains(e.target) && !fab.contains(e.target)) pop.classList.remove('is-open');
  });

  /* ---------- корзина и тосты ---------- */
  window.ETRA_TOAST = function (msg) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    document.getElementById('toastMsg').textContent = msg;
    toast.classList.add('is-show');
    clearTimeout(window.__toastT);
    window.__toastT = setTimeout(() => toast.classList.remove('is-show'), 2600);
  };
  window.ETRA_ADD = function (id) {
    ETRA_CART.add(id);
    const p = (window.ETRA_PRODUCTS || []).find(x => x.id === id);
    ETRA_TOAST(p ? `«${p.name}» — в корзине` : 'Добавлено в корзину');
  };
  document.querySelectorAll('[data-cart-open]').forEach(el => {
    el.addEventListener('click', e => {
      e.preventDefault();
      const n = ETRA_CART.count();
      ETRA_TOAST(n ? `В корзине ${n} шт. — оформление подключается к существующему /checkout` : 'Корзина пока пуста');
    });
  });
  ETRA_CART.refresh();

  /* ---------- карточка товара: общий рендер ---------- */
  window.ETRA_CARD = function (p) {
    const badges = [
      ...(p.badges || []).map(b => `<span class="badge">${b}</span>`),
      ...(p.state ? [`<span class="badge badge-state">${p.state}</span>`] : [])
    ].join('');
    return `
      <article class="p-card" data-reveal>
        <a class="p-card-img" href="product.html?id=${p.id}">
          <div class="p-badges">${badges}</div>
          <img src="${p.img}" alt="${p.name}" loading="lazy">
        </a>
        <div class="p-card-body">
          <h3><a href="product.html?id=${p.id}">${p.name}</a></h3>
          <span class="p-task">${p.task}</span>
          <div class="p-bottom">
            <span class="p-price">${p.price.toLocaleString('ru-RU')} ₽ <small>· ${p.volume[0]}</small></span>
            <button class="p-add" aria-label="В корзину" onclick="ETRA_ADD('${p.id}')">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 5v14M5 12h14"/></svg>
            </button>
          </div>
        </div>
      </article>`;
  };
})();
