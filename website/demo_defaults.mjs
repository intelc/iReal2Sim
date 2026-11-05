// Called after the demo's original viewer initialization has completed.
// These defaults change display state, preserving camera poses and actor gates.
export async function applyDemoDefaults(sceneLayers) {
  for (const id of ['showPath', 'showFrustum', 'showSceneOutlines', 'showStructureOutlines', 'showItemOutlines']) {
    const control = document.getElementById(id);
    if (control) control.checked = true;
  }
  // Wait for the requested view, including scan geometry and its texture atlas.
  // A click alone returns before the asynchronous layer switch finishes.
  if (sceneLayers) {
    if (!await sceneLayers.choose('overlay')) {
      const message = document.getElementById('error')?.textContent || 'The captured scene could not be loaded.';
      throw new Error(message.replace(/^Error:\s*/, ''));
    }
  } else {
    document.getElementById('showOverlay')?.click();
  }
  document.body.dataset.defaultSceneView = 'overlay';
}
