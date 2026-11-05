// All controls are local. No path, prompt or credential is submitted to a server.
const $ = id => document.getElementById(id);
const modeData = {
  overlay: { src: '/media/room-overlay.png', label: 'Overlay · compare the model with the captured reconstruction.', alt: 'Teal modeled walls, kitchen island and furniture aligned over the captured 3D room, with its camera path and frustum' },
  real: { src: '/media/room-real.png', label: 'Captured 3D · inspect the reconstructed surfaces and remaining gaps.', alt: 'The captured RGB-textured reconstruction of the kitchen and living room, including gaps in observed geometry' },
  model: { src: '/media/room-model.png', label: '3D model · inspect authored structure, furniture and fixtures.', alt: 'The modeled room with teal outlines around walls, floor, kitchen cabinets, the island and furniture' },
};
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
for (const button of document.querySelectorAll('[data-mode]')) {
  button.addEventListener('click', () => {
    const mode = modeData[button.dataset.mode];
    if (!mode) return;
    for (const other of document.querySelectorAll('[data-mode]')) other.setAttribute('aria-pressed', String(other === button));
    const image = $('roomPreview');
    image.src = mode.src; image.alt = mode.alt; $('previewDescription').textContent = mode.label;
    if (!reduceMotion.matches) image.animate([{ opacity: .4, transform: 'scale(1.015)' }, { opacity: 1, transform: 'none' }], { duration: 280, easing: 'ease-out' });
  });
}

function makePrompt() {
  const path = $('recordingPath').value.trim().replace(/[\r\n]+/g, ' ');
  const review = document.querySelector('input[name="profile"]:checked')?.value === 'review';
  $('reviewNotice').hidden = !review;
  $('copyStatus').textContent = '';
  const source = path ? `My exported session folder is ${JSON.stringify(path)}.` : 'Help me locate the complete exported session folder.';
  const task = review
    ? 'Run the scan pipeline and an OpenAI capture review. Explain the storyboard upload and help me configure the API key locally.'
    : 'Run the local scan pipeline. Ask before optional API review.';
  $('agentPrompt').value = `Set up iReal2Sim and process my exported iPhone recording. Check dependencies and required credentials without printing values. ${source} ${task} Open the scene dashboard. Keep unavailable fitting stages pending and show me the exact resume command.`;
}
$('recordingPath').addEventListener('input', makePrompt);
for (const input of document.querySelectorAll('input[name="profile"]')) input.addEventListener('change', makePrompt);
$('copyPrompt').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('agentPrompt').value);
    $('copyStatus').textContent = 'Copied. Paste it into your agent.';
  } catch {
    $('agentPrompt').focus(); $('agentPrompt').select();
    $('copyStatus').textContent = 'Prompt selected. Press ⌘C or Ctrl+C to copy.';
  }
});
makePrompt();

// Old links to a specific replay time continue into the demo.
if (new URLSearchParams(location.search).has('t')) location.replace('/demo/' + location.search + location.hash);

// A real source clip provides motion; saving data and reduced motion opt out.
const clip = $('captureClip');
if ('IntersectionObserver' in window) {
  const videoObserver = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (entry.isIntersecting && !reduceMotion.matches && !navigator.connection?.saveData) clip.play().catch(() => {});
      else clip.pause();
    }
  }, { threshold: .4 });
  videoObserver.observe(clip);
  const entrances = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entrances.unobserve(entry.target);
      if (reduceMotion.matches) continue;
      // The phone preview spans the viewport; enter vertically to keep it within it.
      const image = entry.target.classList.contains('scene-preview') && matchMedia('(min-width: 801px)').matches;
      entry.target.animate(image
        ? [{ opacity: .15, transform: 'translateX(24px) scale(1.025)' }, { opacity: 1, transform: 'none' }]
        : [{ opacity: .2, transform: 'translateY(18px)' }, { opacity: 1, transform: 'none' }],
      { duration: image ? 850 : 600, easing: 'cubic-bezier(.22,1,.36,1)', delay: Number(entry.target.dataset.delay || 0) });
    }
  }, { threshold: .12 });
  document.querySelectorAll('[data-reveal]').forEach((node, i) => { node.dataset.delay = String((i % 3) * 65); entrances.observe(node); });
}
