# Third-party software and assets

The portable source release contains our capture/processing code and references
to separately installed dependencies. Their upstream licenses apply:

- [RTAB-Map](https://github.com/introlab/rtabmap): BSD-3-Clause.
- [Open3D](https://github.com/isl-org/Open3D): MIT.
- [OpenCV](https://github.com/opencv/opencv): Apache-2.0 for current releases.
- [NumPy](https://github.com/numpy/numpy) and [SciPy](https://github.com/scipy/scipy): BSD-3-Clause.
- [Trimesh](https://github.com/mikedh/trimesh): MIT.
- [Pillow](https://github.com/python-pillow/Pillow): HPND.
- [Matplotlib](https://github.com/matplotlib/matplotlib): Matplotlib license.
- [Requests](https://github.com/psf/requests): Apache-2.0.
- [FFmpeg](https://ffmpeg.org/legal.html): license depends on the installed build.

No dependency binaries or model checkpoints are bundled. Optional research
dependencies such as HaWoR/MANO, SAM, FoundationPose, Pi3X and Gaussian splatting
are not required by this release and are not redistributed. MANO requires its own
license/access; an API key does not grant rights to those assets.

SceneHands motivates the research direction; its code/data are not included.
Cork's README was studied for the self-compilation documentation structure only.
