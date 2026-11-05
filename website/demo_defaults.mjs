// Called after the demo's original viewer initialization has completed.
// These defaults change display state, preserving camera poses and actor gates.
export function applyDemoDefaults() {
  for (const id of ['showPath', 'showFrustum', 'showSceneOutlines', 'showStructureOutlines', 'showItemOutlines']) {
    const control = document.getElementById(id);
    if (control) control.checked = true;
  }
  document.getElementById('showOverlay')?.click();
  document.body.dataset.defaultSceneView = 'overlay';
}
