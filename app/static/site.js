// Small enhancements; every page works without them.

// Quiz: tidy what's pasted into the handle field (spaces, invisible characters, an @, a profile
// link) into the bare handle; the server does the same, so this only keeps the field honest.
function cleanHandle(raw) {
  let t = raw.replace(/[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff\u00a0]/g, '').trim().replace(/^@\s+/, '@');
  const m = t.match(/^(?:https?:\/\/)?(?:www\.|mobile\.)?(?:x|twitter)\.com\/@?([A-Za-z0-9_]{1,15})(?:[/?#].*)?$/i);
  if (m) return m[1];
  return /^@?[A-Za-z0-9_]{1,15}$/.test(t) ? t.replace(/^@/, '') : t;
}
const form = document.querySelector('form.make');
if (form) {
  const field = form.querySelector('#handle');
  const tidy = () => { field.value = cleanHandle(field.value); };
  // Once a handle is entered, start fetching its posts while the rest of the quiz is answered.
  const prefetched = new Set();
  const prefetch = () => {
    const h = cleanHandle(field.value);
    if (!/^[A-Za-z0-9_]{1,15}$/.test(h) || prefetched.has(h.toLowerCase())) return;
    prefetched.add(h.toLowerCase());
    const body = new FormData(); body.append('handle', h);
    fetch(form.action.replace(/\/make$/, '/prefetch'), { method: 'POST', body, keepalive: true }).catch(() => {});
  };
  field.addEventListener('paste', () => setTimeout(tidy));
  field.addEventListener('blur', () => { tidy(); prefetch(); });
  form.addEventListener('submit', tidy);
  form.addEventListener('submit', e => {
    const b = e.target.querySelector('button[type=submit]');
    b.disabled = true;
    b.textContent = 'Starting…';
  });
}

// Making: poll until the spread is done or has failed, then reload to show it.
const making = document.querySelector('[data-status-url]');
if (making) {
  const order = [...making.querySelectorAll('[data-stage]')].map(li => li.dataset.stage);
  const slot = slotMachine(making.querySelector('[data-slot]'));
  const poll = async () => {
    try {
      const r = await fetch(making.dataset.statusUrl, { cache: 'no-store' });
      const { status, picture } = await r.json();
      if (status === 'done' || status === 'failed' || status === 'missing') return location.reload();
      if (slot) slot.update(status, picture);
      const at = order.indexOf(status);
      making.querySelectorAll('[data-stage]').forEach((li, i) => {
        li.className = i < at ? 'done' : i === at ? 'now' : '';
      });
    } catch (e) { /* try again next tick */ }
    setTimeout(poll, 1500);
  };
  setTimeout(poll, 1000);
}

// Progress page: library pictures flicker past in a taped frame, slow down while the press
// chooses, and land on the spread's actual main picture once it's known.
function slotMachine(el) {
  if (!el) return null;
  const img = el.querySelector('img'), cap = el.querySelector('[data-slot-cap]');
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const reel = Array.from({ length: +el.dataset.reelCount }, (_, i) => el.dataset.reel + String(i + 1).padStart(2, '0') + '.jpg');
  for (let i = reel.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [reel[i], reel[j]] = [reel[j], reel[i]]; }
  reel.forEach(src => { new Image().src = src; });
  let n = 0, delay = 190, landing = null, landed = false, timer;
  const tick = () => {
    if (landed) return;
    if (landing) {
      delay *= 1.22;
      if (delay > 700) {           // slowed right down: land on the real picture
        img.src = landing; landed = true;
        el.classList.add('landed'); cap.textContent = 'this one';
        return;
      }
    }
    img.src = reel[n++ % reel.length];
    timer = setTimeout(tick, delay);
  };
  if (!still) timer = setTimeout(tick, delay);
  return {
    update(status, picture) {
      if (status === 'composing' && !landing) { delay = Math.max(delay, 280); cap.textContent = 'choosing…'; }
      if (picture && !landing) {
        landing = picture;
        const pre = new Image(); pre.src = picture;
        if (still) { img.src = picture; landed = true; el.classList.add('landed'); cap.textContent = 'this one'; }
        else cap.textContent = 'choosing…';
      }
    },
  };
}

// Share page: copy the link.
document.querySelectorAll('[data-copy]').forEach(b => {
  b.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(b.dataset.copy);
      b.textContent = 'Copied';
    } catch (e) {
      prompt('Copy this link:', b.dataset.copy);
    }
  });
});
