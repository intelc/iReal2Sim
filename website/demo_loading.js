// Loaded before the viewer's module graph. The HTML already shows a loading state.
(() => {
  const panel = document.getElementById('demoLoader');
  const main = document.querySelector('main');
  const title = document.getElementById('demoLoadTitle');
  const detail = document.getElementById('demoLoadDetail');
  const progress = document.getElementById('demoLoadProgress');
  const retry = document.getElementById('demoLoadRetry');
  const stages = {
    model: [1, 'Loading the room model', 'Preparing walls, furniture and outlines.'],
    interactions: [2, 'Loading recorded interactions', 'Preparing the hand and object estimates.'],
    capture: [3, 'Loading the captured scene', 'Downloading the reconstruction for the overlay.'],
    textures: [4, 'Preparing the scene textures', 'Adding the captured appearance to the room.'],
  };
  let settled = false;
  main.inert = true;
  main.setAttribute('aria-busy', 'true');
  document.body.dataset.demoLoadState = 'loading';
  retry.onclick = () => location.reload();
  const slowTimer = setTimeout(() => {
    if (settled) return;
    document.getElementById('demoLoadNote').textContent = 'Still loading. Larger scene assets can take a moment on a slower connection.';
    retry.hidden = false;
  }, 20000);

  function fail(error) {
    if (settled) return;
    settled = true;
    clearTimeout(slowTimer);
    panel.dataset.state = 'error';
    document.body.dataset.demoLoadState = 'error';
    main.setAttribute('aria-busy', 'false');
    title.textContent = 'The scene couldn’t load';
    detail.textContent = error?.message || 'A required scene asset is unavailable.';
    retry.hidden = false;
  }

  window.ireal2simLoading = {
    stage(name) {
      if (settled || !stages[name]) return;
      const [step, heading, description] = stages[name];
      title.textContent = heading;
      detail.textContent = description;
      progress.setAttribute('aria-valuenow', String(step - 1));
      progress.setAttribute('aria-valuetext', `${heading}. Step ${step} of 4.`);
      for (const item of progress.querySelectorAll('li')) {
        const index = Number(item.dataset.step);
        item.toggleAttribute('data-complete', index < step);
        item.toggleAttribute('data-active', index === step);
      }
      document.body.dataset.demoLoadStage = name;
    },
    ready() {
      if (settled) return;
      settled = true;
      clearTimeout(slowTimer);
      progress.setAttribute('aria-valuenow', '4');
      progress.setAttribute('aria-valuetext', 'Scene ready');
      title.textContent = 'Scene ready';
      main.inert = false;
      main.setAttribute('aria-busy', 'false');
      document.body.dataset.demoLoadState = 'ready';
      const focused = panel.contains(document.activeElement);
      panel.hidden = true;
      if (focused) document.getElementById('mesh').focus();
    },
    fail,
  };
  // A missing runtime module must show a recoverable state too.
  window.addEventListener('error', event => {
    if (event.target instanceof HTMLScriptElement) fail(new Error('A viewer component could not be downloaded.'));
  }, true);
  window.addEventListener('unhandledrejection', event => fail(event.reason));
})();
