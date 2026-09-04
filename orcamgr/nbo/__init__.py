"""
Natural-orbital bonding analysis (NPA / NBO) computed by ORCAdesk itself.

Kept out of ``core/`` on purpose, like :mod:`orcamgr.mlip` and
:mod:`orcamgr.crest`: this is not a step of the ORCA pipeline
(generate input -> run -> parse a ``.out``) but a self-contained theory stack
that *post-processes a converged wavefunction*, the same way
:mod:`orcamgr.core.plot` post-processes a ``.gbw`` into a cube. Nothing here
touches the queue, the store, or Qt; the only dependency is numpy.

The motivation is licensing: ORCA's ``! NBO`` keyword is an *interface* that
writes a FILE.47 and shells out to the user's own ``gennbo`` executable, which
is separately licensed from the University of Wisconsin. The underlying methods
are published (Reed, Weinstock & Weinhold, *J. Chem. Phys.* **83**, 735 (1985);
Reed, Curtiss & Weinhold, *Chem. Rev.* **88**, 899 (1988)) and are reimplemented
here from those papers. Numbers are therefore ORCAdesk's own and are *not*
claimed to be bit-compatible with the NBO program.

Layers, bottom up:

* :mod:`~orcamgr.nbo.wavefunction` - read a converged wavefunction and recover
  the overlap, density and Fock matrices it implies.
* :mod:`~orcamgr.nbo.nao` - build the natural atomic orbitals from those, and
  the NPA charges they give.
* :mod:`~orcamgr.nbo.population` - read chemistry off them: Wiberg bond orders,
  natural valences, natural electron configurations.
* :mod:`~orcamgr.nbo.lewis` - find the Lewis structure in that density (cores,
  lone pairs, bonds with their hybrids) and complete it into a full orthonormal
  NBO basis (antibonds, leftover valence, Rydberg).
* :mod:`~orcamgr.nbo.perturbation` - the second-order donor/acceptor table: the
  Fock matrix in the NBO basis, one formula, kcal/mol.
* :mod:`~orcamgr.nbo.source` - the way in: convert a finished run's ``.gbw``
  with ``orca_2mkl``, so nothing above ever has to know where a wavefunction
  comes from.
* :mod:`~orcamgr.nbo.analysis` - the way out: one result object for the whole
  analysis, cached beside the run and serializable for the bridge.

The dependency direction is strictly upward, and nothing here imports Qt or
touches the queue. A caller normally wants only
:func:`~orcamgr.nbo.analysis.analysis_for`.
"""
