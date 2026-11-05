# Public landing page

The buildless landing page lives at https://ireal2sim.intelchen.com/ and the
interactive scene at /demo/. The public source ZIP remains under /downloads/.

HTML/CSS/JS here contain no API credentials or private capture files. Images and
the demo are derived publication assets, assembled separately from the reviewed
viewer by the deployment export. They are excluded from the source package.

The landing page uses the existing teal/Avenir visual system, actual source
imagery and viewer renders. The comparison and agent prompt work in the browser;
processing new recordings runs locally from the downloaded source.

`demo_defaults.mjs` applies Overlay, camera path/frustum and outline defaults
after the demo's existing initialization. Geometry and actor quality gates stay
in the original viewer implementation.

`demo_loading.html`, `.css` and `.js` provide an immediate loading screen before
the viewer modules run. The publication export connects its four stages to the
actual model, interaction, scan and texture loads. The screen closes only after
the default overlay has loaded and rendered. Failed loads offer a reload action;
slow connections keep their current stage visible without inventing a percentage.
