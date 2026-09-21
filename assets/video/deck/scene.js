/* Deterministic scene timeline.
 *
 * Every animation is a paused Web Animation or a tick handler, and both are driven
 * from ONE clock, so any moment can be reproduced exactly:
 *   scene.html            plays live, in real time (review)
 *   scene.html?t=12.5     frozen at 12.5 s (review key frames)
 *   scene.html?t=end      frozen at the final state
 *   window.seek(t)        the video renderer steps this frame by frame
 *
 * A scene defines window.build = T => { T.dur = ...; T.at(...); T.tick(...) }.
 * Entrances use fill 'both' (hidden until they start); exits must use T.out, which
 * fills 'forwards' only - a later 'both' animation would back-fill over an earlier
 * entrance and show an element before its time.
 */
(() => {
  const ticks = [];
  const T = {
    dur: 10, anims: [], now: 0,
    at(target, start, dur, keyframes, opts = {}) {
      const els = typeof target === 'string' ? document.querySelectorAll(target)
                : (target instanceof Element ? [target] : target);
      els.forEach(el => {
        const a = el.animate(keyframes, {
          delay: start * 1000, duration: dur * 1000, fill: opts.fill || 'both',
          easing: opts.easing || 'cubic-bezier(.2,.7,.2,1)' });
        a.pause();
        T.anims.push(a);
      });
    },
    out(target, start, dur, keyframes, opts = {}) { T.at(target, start, dur, keyframes, { ...opts, fill: 'forwards' }); },
    fadeUp(target, start, dur = 0.7, dy = 26) {
      T.at(target, start, dur, [{ opacity: 0, transform: `translateY(${dy}px)` }, { opacity: 1, transform: 'none' }]);
    },
    fadeIn(target, start, dur = 0.6) { T.at(target, start, dur, [{ opacity: 0 }, { opacity: 1 }]); },
    fadeOut(target, start, dur = 0.5) { T.out(target, start, dur, [{ opacity: 1 }, { opacity: 0 }]); },
    tick(fn) { ticks.push(fn); },
    seek(t) {
      T.now = t;
      T.anims.forEach(a => { a.currentTime = t * 1000; });
      ticks.forEach(fn => fn(t));
    },
    play() {
      cancelAnimationFrame(T.raf);
      const t0 = performance.now();
      const loop = () => {
        const t = (performance.now() - t0) / 1000;
        T.seek(Math.min(t, T.dur));
        if (t < T.dur) T.raf = requestAnimationFrame(loop);
      };
      loop();
    },
  };
  // eased 0..1 progress of t within [a, b] - for tick-driven counters and typing
  T.p = (t, a, b) => Math.min(1, Math.max(0, (t - a) / (b - a)));
  T.ease = x => 1 - Math.pow(1 - x, 3);

  window.T = T;
  window.seek = t => T.seek(t);
  window.addEventListener('message', e => {
    if (e.data === 'replay') T.play();
    else if (e.data && typeof e.data.seek === 'number') T.seek(e.data.seek);
  });
  document.fonts.ready.then(() => {
    if (typeof window.build === 'function') window.build(T);
    const q = new URLSearchParams(location.search).get('t');
    if (q === null) T.play();
    else T.seek(q === 'end' ? T.dur : parseFloat(q));
    document.documentElement.dataset.ready = '1';
  });
})();
