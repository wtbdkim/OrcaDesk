"""
2D drawing add-on: a ChemDraw-style 2D drawing of a structure, in exactly the
orientation the 3D view shows it, with wedge/hash bonds chosen from how far
each bond leaves the screen plane.

An **add-on**, kept apart from the core the way the MLIP support is
(``orcamgr/mlip/``): the one thing it needs that ORCAdesk does not ship is
RDKit (bond-order perception), and RDKit is never added to ORCAdesk's own
requirements or to the PyInstaller build. The user registers -- or lets
ORCAdesk create -- a separate Python environment that has it, and ORCAdesk runs
a small worker script there as a subprocess (JSON on stdin, JSON on stdout).

Contents, all Qt-free:

* :mod:`orcamgr.draw2d.env` -- the import probe behind the Settings card and
  the Visual panel's "not set / ready / error" state;
* :mod:`orcamgr.draw2d.installer` -- Create: a venv under user_data_root plus
  ``pip install rdkit``;
* :mod:`orcamgr.draw2d.worker` -- the worker script (a string constant, run
  with ``python -c``, so nothing has to be found on disk in a frozen build) and
  the launcher that validates what it prints;
* :mod:`orcamgr.draw2d.molfile` -- the MDL V2000 writer behind *Save MOL*.

The projection, the stereo rule and the SVG are NOT here: they run in the
front-end (``web/next/draw2d.js``) on every redraw so the drawing follows a
drag in real time. The MOL writer takes the coordinates and wedge flags the
front-end just drew, so there is one projection, not two.
"""
