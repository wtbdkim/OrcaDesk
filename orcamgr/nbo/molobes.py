"""
One molecular orbital as per-atom lobes: what the 2D drawing's orbital
overlay draws (web/next/draw2d.js), the textbook picture of an MO rather than
a projected isosurface.

A textbook MO diagram draws, on every atom that carries part of the orbital,
a pair of p lobes (or an s circle) sized by how much of the orbital sits there
and coloured by its phase. That needs three numbers per atom, and all three
come from the wavefunction ORCAdesk already reads for its NBO analysis
(:class:`~orcamgr.nbo.wavefunction.Wavefunction`), with no grid and no
integral:

* **share** -- the atom's part of the orbital. Löwdin's: the MO's
  coefficients in the symmetrically orthogonalized basis are
  ``c' = S^1/2 c``; since ``c^T S c = 1``, ``sum(c'^2) = 1`` exactly, and the
  squares summed over an atom's functions are that atom's share. Split by
  angular momentum into ``s_share`` / ``p_share`` / ``d_share`` (d *and
  higher*: a d or f share is folded into the atom's share, not drawn -- the
  UI says "mostly d" instead). Löwdin rather than Mulliken because Mulliken
  shares of one orbital can be negative or exceed 1, and a negative lobe size
  is not a thing.
* **s** -- the s amplitude: ``sqrt(s_share)``, signed by the atom's largest
  s coefficient (in ``c'``). The largest rather than the sum, because a
  valence orbital's core-like and valence-like s functions can carry opposite
  signs and nearly cancel.
* **p** -- the p vector in Cartesian axes (Bohr frame of the file, which is
  the run's own axes): the principal axis of the atom's p shells' coefficient
  vectors ``v_k = (c'_px, c'_py, c'_pz)`` (largest eigenvector of
  ``sum v_k v_k^T``), pointed the way its largest shell points, with length
  ``sqrt(p_share)``. The orbital is positive on the ``+p`` side of the atom.

  Molden writes p in the order px, py, pz even in a spherical file;
  :func:`~orcamgr.nbo.wavefunction._spherical_m` records those as
  ``m = +1, -1, 0``, and :func:`~orcamgr.nbo.grid.real_solid_harmonics` maps
  ``+1 -> x, -1 -> y, 0 -> z`` -- the table pinned against orca_plot's own
  cubes to 5e-6 (tests/test_nbo_grid.py), with no p sign flip
  (``_ORCA_SIGN`` has none for l = 1). :data:`P_AXIS` is that mapping, and
  tests/test_molobes.py checks the drawn sign against the orbital's value
  evaluated on a grid.

**Signs are taken on the basis functions as drawn, not on the raw
coefficients.** ORCA writes some contractions with every coefficient
negative (def2-SVP nitrogen's inner p shell, measured on the formamide
fixture: -2.19, -1.75, -0.81), so that function is -|phi| everywhere and a
positive coefficient on it is a NEGATIVE lobe. Each function's phase is the
sign of its radial part weighted toward the valence region,
``sum_i c_i a_i^-(l+3)/2`` (the integral of ``R(r) r^(l+2)``, which a 2s-like
contraction's outer lobe dominates), and the Löwdin coefficients are
multiplied by it before any sign or direction is read. tests/test_molobes.py
checks the result against the orbital's own value on a grid.

The overall phase of an MO is arbitrary (ORCA may flip it run to run); within
one orbital it is consistent, which is all a lobe picture needs.

Pure numpy, Qt-free: the bridge (``get_mo_lobes``) builds a :class:`MoLobeSet`
once per wavefunction on a background thread -- ``S^1/2`` is the one O(n^3)
step -- and every orbital after that is one matrix-vector product.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .wavefunction import (BOHR_PER_ANGSTROM, Wavefunction, WavefunctionError,
                           _matrix_power)

#: ORCA's own printed eV (core/parser.py uses the same CODATA value).
HARTREE_TO_EV = 27.211386245988

#: Molden p component -> Cartesian axis, keyed by the m the reader stores
#: (wavefunction._spherical_m(1) == [1, -1, 0] for px, py, pz).
P_AXIS = {1: 0, -1: 1, 0: 2}

#: An orbital counts as occupied above this occupation -- the parser's own
#: threshold (core/parser.py _parse_orbitals), so HOMO means the same orbital.
OCC_THRESHOLD = 0.01


def shell_phases(wf: Wavefunction) -> np.ndarray:
    """+1 / -1 per basis function: the sign of its shell's radial part,
    weighted toward the valence region (``sum_i c_i a_i^-(l+3)/2``, i.e. the
    integral of ``R(r) r^(l+2)``). See the module note: ORCA writes some
    contractions all-negative."""
    signs = []
    for shell in wf.shells:
        w = sum(c * a ** (-(shell.l + 3) / 2.0)
                for a, c in zip(shell.exponents, shell.coefficients))
        signs.append(-1.0 if w < 0 else 1.0)
    return np.array(signs)[wf.bf_shell] if signs else np.ones(wf.n_basis)


def _spin_key(spin: str) -> str:
    """"alpha"/"beta" from either spelling: the parse's ""/"a"/"b" or the
    wavefunction's own names."""
    s = (spin or "").strip().lower()
    if s in ("", "a", "alpha", "0"):
        return "alpha"
    if s in ("b", "beta", "1"):
        return "beta"
    raise WavefunctionError(f"unknown spin {spin!r} -- expected alpha or beta.")


@dataclass
class MoLobeSet:
    """A wavefunction plus its ``S^1/2`` -- everything a lobe needs."""

    wavefunction: Wavefunction
    s_half: np.ndarray
    phase: np.ndarray                     # (nbf,) +-1: each function's radial sign
    source_mtime: float = 0.0

    @classmethod
    def from_wavefunction(cls, wf: Wavefunction,
                          source_mtime: float = 0.0) -> "MoLobeSet":
        """Raises WavefunctionError (a non-square C, a singular overlap)."""
        return cls(wavefunction=wf, s_half=_matrix_power(wf.overlap(), 0.5),
                   phase=shell_phases(wf), source_mtime=source_mtime)

    # ---- the orbital list -------------------------------------------------

    def orbital_list(self) -> list[dict]:
        """Every orbital, named the way the Results › Visual Orbitals tile
        names them (web/next/mvwin.js picksFor): the frontier pair is taken by
        ENERGY across both manifolds, as the parser does; in the HOMO's
        manifold an orbital at or below it is HOMO / HOMO−k, in the LUMO's
        manifold one at or above it is LUMO / LUMO+k, anything else "#idx".
        An unrestricted run tags each name α / β."""
        wf = self.wavefunction
        restricted = wf.restricted
        rows = []
        for key in wf.spins:
            tag = "" if restricted else ("a" if key == "alpha" else "b")
            for i, (e, n) in enumerate(zip(wf.energies[key], wf.occupations[key])):
                rows.append({"index": i, "spin": tag, "ev": float(e) * HARTREE_TO_EV,
                             "occ": float(n)})
        occ = [r for r in rows if r["occ"] > OCC_THRESHOLD]
        vir = [r for r in rows if r["occ"] <= OCC_THRESHOLD]
        homo = max(occ, key=lambda r: r["ev"]) if occ else None
        lumo = min(vir, key=lambda r: r["ev"]) if vir else None
        for r in rows:
            name = f"#{r['index']}"
            if homo and r["spin"] == homo["spin"] and r["index"] <= homo["index"]:
                k = homo["index"] - r["index"]
                name = "HOMO" if not k else f"HOMO−{k}"
            elif lumo and r["spin"] == lumo["spin"] and r["index"] >= lumo["index"]:
                k = r["index"] - lumo["index"]
                name = "LUMO" if not k else f"LUMO+{k}"
            tag = "" if restricted else ("α " if r["spin"] == "a" else "β ")
            r["label"] = tag + name
            r["frontier"] = ("homo" if r is homo else "lumo" if r is lumo else "")
            r["ev"] = round(r["ev"], 4)
        return rows

    # ---- one orbital --------------------------------------------------------

    def lobes(self, spin: str, index: int) -> dict:
        """Per-atom share / s / p of orbital ``index`` of ``spin``.

        Returns ``{spin, index, energy_ev, occupation, norm, atoms: [{share,
        s, p, s_share, p_share, d_share}]}``; ``norm`` is the shares' sum
        (1 to rounding -- a check, not a fudge). Raises WavefunctionError for
        an orbital the file does not have."""
        wf = self.wavefunction
        key = wf._resolve_spin(_spin_key(spin))
        c = wf.coefficients[key]
        if not 0 <= int(index) < c.shape[1]:
            raise WavefunctionError(
                f"this wavefunction has no orbital {index} "
                f"({c.shape[1]} {key} orbitals).")
        index = int(index)
        cl = (self.s_half @ c[:, index]) * self.phase
        sq = cl * cl
        atoms = []
        for a in range(wf.n_atoms):
            on = wf.bf_atom == a
            s_mask = on & (wf.bf_l == 0)
            p_mask = on & (wf.bf_l == 1)
            d_mask = on & (wf.bf_l >= 2)
            s_share = float(sq[s_mask].sum())
            p_share = float(sq[p_mask].sum())
            d_share = float(sq[d_mask].sum())
            # s: sqrt(share), signed by the largest s coefficient
            s_amp = 0.0
            if s_mask.any():
                cs = cl[s_mask]
                s_amp = float(np.sign(cs[np.argmax(np.abs(cs))]) * np.sqrt(s_share))
            atoms.append({
                "share": round(s_share + p_share + d_share, 6),
                "s": round(s_amp, 6),
                "p": [round(float(v), 6) for v in self._p_vector(cl, p_mask, p_share)],
                "s_share": round(s_share, 6),
                "p_share": round(p_share, 6),
                "d_share": round(d_share, 6),
            })
        return {
            "spin": key, "index": index,
            "energy_ev": round(float(wf.energies[key][index]) * HARTREE_TO_EV, 4),
            "occupation": float(wf.occupations[key][index]),
            "norm": round(float(sq.sum()), 6),
            "atoms": atoms,
        }

    def _p_vector(self, cl: np.ndarray, p_mask: np.ndarray, p_share: float) -> np.ndarray:
        """The principal p axis of one atom, length sqrt(p_share), pointing the
        way the atom's largest p shell points (the orbital's + side)."""
        wf = self.wavefunction
        if p_share <= 0.0:
            return np.zeros(3)
        vectors = []
        for shell in np.unique(wf.bf_shell[p_mask]):
            v = np.zeros(3)
            for k in np.nonzero(wf.bf_shell == shell)[0]:
                v[P_AXIS[int(wf.bf_m[k])]] = cl[k]
            vectors.append(v)
        vs = np.array(vectors)
        w, vecs = np.linalg.eigh(vs.T @ vs)
        axis = vecs[:, int(np.argmax(w))]
        lead = vs[int(np.argmax(np.linalg.norm(vs, axis=1)))]
        if float(axis @ lead) < 0.0:
            axis = -axis
        return axis * np.sqrt(p_share)

    # ---- the geometry the orbitals belong to ------------------------------

    def elements(self) -> list[str]:
        return list(self.wavefunction.elements)

    def coordinates_angstrom(self) -> list[list[float]]:
        xyz = np.asarray(self.wavefunction.coordinates) / BOHR_PER_ANGSTROM
        return [[round(float(v), 5) for v in row] for row in xyz]
