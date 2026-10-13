# Magnetic TOF analysis with native subgroup Rietveld refinement

## Product surface vs engine API

Agents do not call `workflow.analyze` directly. The shipped capability is the
`magnetic-structure-analysis` skill plus five MCP tools
(`InitializeMagneticMission`, `SearchPropagationVector`, `EnumerateMagneticCandidates`,
`RefineMagneticCandidates`, `ExportMagneticStructure`) defined in
`tools/magnetic_structure_mcp.py` on top of [`mission.py`](mission.py). The mission layer
owns session/mission paths (`WORKDIR/<user_id>/<session_id>/<mission_name>/`), input
pairing, per-stage PNG figures (`present.py`) and the comparison strategy; this README
documents the engine underneath it.

`workflow.analyze` is the small, JSON-serializable staged API that layer builds on:

```python
from magnetic_analysis.workflow import analyze

analyze(case_directory, output_directory, stage="inspect")
analyze(case_directory, output_directory, stage="search")
analyze(case_directory, output_directory, stage="candidates",
        magnetic_elements=["Fe"], max_candidates=256)
result = analyze(case_directory, output_directory, stage="refine",
                 cycles=24, max_models=256, time_limit_seconds=1800)
print(result["final_mcif_path"], result["final_gpx_path"], result["report_path"])
```

Put `tools/GSAS-II-master` on `PYTHONPATH`. Use the installed GSAS-II-compatible
interpreter with numpy, scipy, spglib, ASE and (for propagation search) seekpath.
The native candidate stage also uses GSAS-II's `requests` transport.
`threadpoolctl` bounds BLAS to one thread during each GSAS-II refinement without
changing the model, bins, precision, cycle budget or enumerated candidates.
An exact-input 128 MiB LRU cache reuses repeated TOF convolutions and derivatives
within model comparison, copies returned arrays, and restores original GSAS
functions on exit. Public-profile verification measured 42.02→11.65 s for an
identical one-cycle calculation with bit-identical profiles; this is not an
approximate profile or subsampling optimization.
## Authoritative default candidate/refinement path

`candidates` calls the existing
`magnetic_structure_candidates.search_magnetic_structure_candidates` with
`exact_wyckoff_orbit=False`, no Landau/maximal/Wyckoff filtering and no finite
orientation list. All returned phase dictionaries, transformations, origins,
magnetic sites and independent orbit degrees of freedom are retained. Candidate
count overflow fails without truncation. Default candidate bound is 256; default
refinement `max_models` is also 256, so the stages accept the same bounded list.
Each phase is persisted as an engine-generated local pickle, not serialized
into the MCP response. Never load caller-provided phase pickles.

Bilbao receives only its generic symmetry operators/k query. The transport
rejects other fields/endpoints; generic HTML responses are cached by query hash.
No atoms, cell lengths, CIF, spectra or experiment metadata are sent. Network
failure is explicit, with **no silent local-enumeration fallback**.

`refine` uses ordinary GSASIIscriptable native Rietveld: high-T nuclear baseline,
low-T nuclear control, then independent native moments/common scale followed by
joint cell/ADP/moment/profile refinement. Both nuclear and magnetic-only phases
are transformed using the wrapper's exact matrix/origin. Native magnetic
symmetry/site constraints are authoritative; independent orbit amplitudes are
not tied to each other. Cell metric and common histogram scales are tied 1:1;
matched nuclear/magnetic ADPs are tied. Native HAP symmetry arrays are regenerated
after transformation. Native refinements, fresh covariance, all-bank residuals,
removed singularities, moment correlation, model failures and BIC are recorded.
Full composition MCIF uses expanded native axial/time-reversal operations.
Final group identifiability is qualified; a path alone is never scientific PASS.
Every successfully fitted candidate exports full-resolution observed/calculated/
difference/background/weight CSV and standalone SVG plots for all banks, with
the nuclear-only control overlaid. Paths are recorded in `fit_evidence_paths`;
the best-model API response includes these paths. Failed candidate evidence is
also persisted when a fresh, finite calculated profile is available.

The total stage budget uses a POSIX alarm, including baseline/model work.
An alarm is delivered when native numerical calls return; external worker
termination remains necessary for an unresponsive compiled routine. Timeout
persists partial diagnostics and does not rank an incomplete comparison.
Persisted `progress` gives completed/total/converged model counts and remaining
wall-clock budget after each candidate; the same checkpoint is printed to stdout.
No conditional SciPy moment/profile/cell solver is on this default path.
Low-T startup copies calibrated high-T bank scales, disables inherited profile
flags, then fits scales/background before cell/ADPs/profile. Candidate moment
startup likewise disables the nuclear-control profile flags before releasing
them for the joint step. Native covariance claiming convergence while
`DelChi2<-0.001` is rejected (except verified numerical residual-floor cases).
This GSAS version can otherwise call an increasing-objective termination
"converged"; such a model must not become a selected magnetic answer.
The native convergence tolerance is GSAS-II's standard `min dM/M=0.001`, not
the legacy helper's 1e-6 override. Positive profile boundaries are clamped and
recomputed rather than exporting a stale, unphysical profile. Bounds apply to
effective `beta(d)`, Gaussian variance and Lorentzian width throughout fitted
bins and reflection centers, not merely to positive `beta-0`, `sig-1` and `X`.
For example, a fixed negative `beta-1` imposes a positive lower `beta-0`
depending on the fitted d range; otherwise GSAS can silently skip reflections.
The derived bounds and active boundaries are persisted for every fit.
HAP scale seeds
are divided by the cell transformation's absolute determinant: GSAS powder
uses a reciprocal-volume correction, so doubling the cell doubles intensity
at unchanged HAP scale. An independent doubled-cell/origin-shift profile test
verifies the resulting representation invariance. Nuclear and magnetic phases
always share the same child-cell scale; no extra magnetic-only normalization
factor is introduced.

### Native derivative and solver verification

The generic tests compare the **constrained, weighted residual** Jacobian with
central differences in a nonorthogonal transformed cell containing two
antiferromagnetic sites. All independent allowed moments, coupled phase scales,
cell metrics, ADPs and TOF profile parameters are exercised. The native Hessian
and gradient are also checked against `J @ J.T` and `-J @ residual`. TOF Fortran
profiles return float32 arrays: finite-difference steps must resolve that
precision; tiny steps can falsely suggest incorrect width derivatives.
Cell derivatives have a looser tolerance because finite reflection windows move.
A native time-reversed inversion orbit synthetic fit recovers its 2.3 μB
moment and reduces the nuclear-only residual to the numerical floor.

The bundled GSAS-II MINPACK (analytic Jacobian/numeric) producer now preserves
its actual `ier` convergence code and objective reduction. It formerly wrote
`converged=None, DelChi2=-1` even for successful MINPACK fits. This is distinct
from genuine increasing-objective Hessian termination, which remains rejected.
Derivative agreement or synthetic recovery does **not** establish a valid
magnetic model for an experimental dataset; failed/singular native fits,
zero-moment boundaries and residual structure must still be reported.
`refine` accepts `native_solver="analytic Hessian"` (GSAS default), `"Hessian SVD"`
or `"analytic Jacobian"`; the selected native solver is applied consistently
to all baselines and candidates and recorded even on failure. This is not a
silent optimizer fallback, nor a custom conditional fitting implementation.

## Retained experimental local helpers (not the default API)

`enumerate_local_candidates` / `refine_local_candidates` retain previously
tested offline collinear controls for explicit research use only. The following
legacy details describe those helpers, not the authoritative native API.
The common moment amplitude initializes by measured-profile weighted least
squares in amplitude squared (two exact nonzero GSAS calculations), rather than
an assumed ordered moment.
If the initial nonnegative amplitude-squared LS optimum is at zero, the
initialization uses its measured one-sigma resolution rather than a tiny moment
that makes GSAS finite-difference derivatives ill-conditioned. This is not a
lower bound on fitted moment; the seed diagnostics explicitly report it.
Those boundary families use exact quadratic magnetic-profile tensors, fitted
in the full symmetry-allowed Cartesian subspace with per-bank scale/background
variable projection. Zero is explicitly compared to nonzero starts and disables
the magnetic-only phase rather than dividing by a zero vector. Nuclear nuisance
parameters are fitted between two conditional quadratic steps; this alternating
scope and optimizer convergence are reported, not misrepresented as full joint
GSAS moment covariance.
The local helpers make no Bilbao query. No material-specific structure,
propagation, moment magnitude, or magnetic-group answer is embedded.

## Input contract

The directory may contain an `input/` subdirectory. Inside that directory:

* `initial.cif`: parent nuclear structure.
* `high_temperature/{bank}.dat`: high-temperature nuclear spectra.
* `low_temperature/{bank}.dat`: corresponding low-temperature spectra.
* `instruments/{bank}.instprm`: uniquely name-matched TOF instrument parameters.
  Instruments can alternatively be directly beside `initial.cif`.

Bank stems must match between temperatures. Instrument stems must match a bank
stem or contain it as a complete separated token. Ambiguous or missing matches
are errors; there is no “first instrument” fallback. Standard GSAS-II-readable
powder formats are accepted, including three-column TOF/intensity/sigma text.
For text spectra, descriptive header lines are explicitly normalized into
persisted `.xye` inputs; nonnumeric rows inside numeric data are errors.
Input directories, CIF, spectra and instrument symlinks must remain within the
requested input tree.
The loaded instrument **must** identify neutron TOF (`PNT`). TOF files are
interpreted using GSAS-II's importer conventions for GSAS count/bin-edge files.
Three-column XY(E) values are point-sampled TOF coordinates/intensity/error:
their positions and final point are retained, without the unconditional
half-bin shift in the bundled scriptable importer.

## Shared inspection/search and legacy local-helper evidence

* **inspect**: loads instruments and real spectra into `inspection.gpx`.
  Fits high→low intensity scale and an affine d-space thermal alignment using
  nuclear peak anchors and robust fitting. Subtracts independently estimated
  smooth backgrounds and detects statistically significant positive difference
  peaks. Saves `inspect_report.json` and per-bank compressed difference arrays.
  Options: `max_strain=0.02`, `significance=5`, `max_peaks=256`,
  `background_window=101`. Severe mismatch, missing anchors, no overlap and
  alignment at its bound raise errors.
* **search**: tests measured extra positions on the complete integer reciprocal
  lattice, deliberately **not** using nuclear systematic-absence filtering:
  nuclear-forbidden integer reflections can be magnetic at k=0. Noninteger
  extra peaks invoke the existing offline GSAS-II k-vector-search wrapper.
  Options: `max_index=8` (maximum 16), `indexing_tolerance=0.01`,
  `search_option=0` or `1`, optional explicit `k_vectors`.
  No extra peaks mean propagation is undetermined, not automatically k=0.
  Explicit vectors are labeled caller hypotheses, never measured conclusions.
  Primary evidence uses non-nuclear-overlapping peaks corroborated across banks
  (where available), with explicit significance thresholds. Lower-confidence
  peaks remain in the report. A common initial-CIF/low-T isotropic lattice
  dilation within ±3% is fitted when at least three positions are available;
  its value is reported and is not a substitute for lattice refinement.
* **candidates**: reuses the existing wrapper's generic CIF loader, magnetic-site
  selector and magnetic-phase constructor, but **not** its network endpoint.
  Enumerates all sign patterns on selected expanded sites, with thirteen
  axis/diagonal orientations plus one generic irrational seed. Global reversal and parent-symmetry-related
  modes are identified; spglib computes their exact axial magnetic stabilizers.
  Uses a normalized **unit crystal-axis** basis, not lattice-length-scaled
  fractional coordinates, and preserves every relative sign and direction.
  Default seed budget: 128, expanded magnetic sites: 1–12. Exceeding a budget
  fails explicitly rather than selecting/truncating favorable candidates.
  Saves `candidates_report.json`, which contains moments and symmetry evidence;
  the API result contains only candidate IDs/group numbers and paths.
  Seeds identify magnetic stabilizers, not fixed final component ratios. The
  exact axial-operation invariant subspace is computed by SVD, and every allowed
  common-vector component is refined. A generic seed includes lowest-symmetry
  continuous-direction families. Source atom-operation codes preserve orbit
  correspondence when nuclear coordinates are refined.
* **refine**: fits high-T nuclear scale/background, optionally lattice, then
  symmetry-allowed coordinates and ADPs (with a polar-origin anchor);
  independently fits low-T nuclear-only control. Each candidate is fitted
  against **all real low-T banks** with GSASIIscriptable, first moments, then
  common scale + moments + ADPs. Magnetic-only scattering phases are
  paired with the nuclear phase, with scales tied 1:1 and matching isotropic
  or symmetry-transformed anisotropic ADPs tied. Selected-atom geometry and
  the low-T instrument profile are shared;
  site signs and equal magnitudes are constrained, while symmetry-allowed
  common-vector components remain free. Only components actually forbidden
  by symmetry are held; zero seed components are not automatically forbidden.
  This bundled GSAS-II computes magnetic phases' magnetic scattering **only**;
  treating them as combined nuclear+magnetic phases would lose nuclear peaks.
  Saves high/low baseline GPX, every candidate GPX/MCIF, calculated-profile
  CSVs, baseline report and incrementally updated `refinement_report.json`.
  `peak_decomposition.json` separates nuclear/magnetic calculated integrated
  areas at magnetic-sensitive and automatically selected nuclear-dominated
  peaks, using actual TOF spacings. Failed modes retain their last solver
  residual/convergence diagnostics when available.
  Known-variance BIC on identical bins compares candidates and the
  nuclear-only control; Rwp, weighted residual sums, parameter count,
  convergence, singular-variable removals, physical moments and competing
  near-degenerate models are reported.
  Every API refinement result includes `scientific_status`, `fit_quality`,
  `failed_models`, and `warning`. `scientific_status.comparison_complete` means
  all attempted models converged; an export path alone never establishes
  a valid or identifiable magnetic group. Severe overdispersion, failed models,
  or highly correlated moment components explicitly mark the result unresolved.
  When the high-T fixed-profile residual is overdispersed, an independently
  fitted effective TOF profile alternative is compared by BIC. This is a nuclear
  sample profile, **not** an independent instrument calibration. Its coefficients
  initialize every low-T model. Alternative failures are explicit in the
  baseline report; unresolved systematic errors qualify magnetic conclusions.
  Isotropic ADPs are bounded nonnegative, and nuclear-only frozen parameters
  are released for independent model comparisons.
  Initial low-T lattice lengths use the median measured high→low thermal
  alignment when inspection is available. Candidate ADPs initialize from the
  high-T baseline, not the potentially magnetically contaminated nuclear-only
  low-T ADPs or coordinates. Common scales initialize on nuclear profile support
  with magnetic extinction for every basis direction (not only the seed);
  overlapping integer reflections and profile tails are included. Insufficient
  support is explicitly reported. The scales are subsequently free on all bins,
  not held to this initialization. ADP bounds are released again before the joint
  step after the moment model changes, with refreshed GSAS parameter IDs.
  Higher-symmetry HStrain arrays are not copied into P1.
  Magnetic form-factor types are preserved from the imported CIF atom types.
  Element symbols without an explicit ionic charge do not uniquely specify an
  oxidation state; charge neutrality alone is not an ion assignment. Missing ion
  metadata is reported and the result remains conditional on the supplied types.
  With `refine_profile=True`, all candidates independently fit positive low-T
  effective `alpha`, `beta-0`, `sig-1`, `X` using the same high-T initialized
  bounds (reference value divided/multiplied by 100; zero reference becomes
  1e-6). Bounds do not expand between passes. Scale and Chebyshev backgrounds
  are variable-projected on the full pattern. Up to three conditional passes
  alternate profile, parent-symmetry common cell, nuclear ADPs and exact
  quadratic moments; both phases share the cell throughout. Stabilization
  requires relative objective change <1e-4; hitting the pass limit is explicitly
  qualified, not called convergence. Each bank adds four nonlinear parameters;
  unique parent reciprocal-metric terms are counted once in BIC. Individual
  fitted values, bounds, evaluations, added counts and pass objectives persist.
  The nuclear-only control has the same conditional low-T profile/cell surface
  and added BIC count; magnetic improvement is not credited for nuisance-profile
  flexibility unavailable to the control. Candidate profile initialization
  always resets to the shared high-T coefficients, not the contaminated
  nuclear-only low-T optimum. This extra nonlinear work increases runtime;
  time limits are checked between complete models and partial comparisons
  remain explicit, never silently truncated into a unique winner.
  Unresolved width/intensity residuals must not be interpreted as unique
  moment-direction evidence. No preferred-orientation correction is inferred
  from a poor residual alone.
  Two coupled one-line GSAS repairs are included: magnetic symmetry table
  appending must occur outside the spin-flip lookup exception (successful lookups
  otherwise produce an empty table); anisotropic `GenAtom` cell translation is
  tuple element 3, not element 2 (the operation ID used for orbit mapping).
  Magnetic scattering and ADP-derivative Cartesian conversion also normalize
  direct-cell basis columns; normalizing the reciprocal metric does not produce
  unit crystal-axis columns in nonorthogonal cells. An independent single-atom
  polarization-factor test verifies intensity and moment derivatives.
  The magnetic forward scattering-vector transform is also `B.T @ h` where
  `B=A^-1`: Miller indices are reciprocal covectors, not direct coordinates.
  The former `B @ h` disagreed with its own magnetic ADP derivative and creates
  incorrect mixed-index polarization in nonorthogonal cells. Independent
  two-site absolute nuclear/magnetic factors, full stabilizer expansion,
  histogram-scale/moment representation invariance, and equal basal-direction
  powder intensity are regression-tested.
  The wrapper's GUI magnetic d-minimum default is overridden to zero: magnetic
  scattering is included over the entire measured range, not cut at 1 Å.

The default refinement bounds are 64 models, at most two passes of 24 cycles per
step, and 1800 seconds. Relative objective tolerance is `1e-6` for reproducible
ranking. Exact synthetic residuals below a reported numerical floor are accepted
without disguising a false original GSAS convergence flag.
Active ADP boundaries permit at most one additional bounded pass per atom to
settle the physical active set; actual passes and frozen parameters are reported.
Copied phases are reindexed before installing parameter bounds. Every fit must
produce fresh GSAS covariance: scriptable solver failures cannot inherit an
earlier successful stage's convergence flag.
The high-T effective-profile comparison fits positive width/tail coefficients
with bounded numerical least squares and exact zero-cycle GSAS calculations;
per-bank nonnegative scale and Chebyshev background are variable-projected.
Correlated three-term variance polynomials are not simultaneously released.
At most 40 nonlinear evaluations per bank are allowed, and nonconvergence
rejects the alternative explicitly. GSAS's post-update ADP clipping
is applied to persisted atoms and the profile is recomputed before ranking.
The wall-time bound is checked **between** refinements; a running GSAS-II
refinement is bounded by cycles rather than killed mid-write. The MCP worker may
enforce a stricter subprocess timeout. Every enumerated model must be considered;
individual failures are persisted explicitly, and an all-failed or incomplete
run raises an error. Stage exceptions also persist `{stage}_error.json`.

## Scientific limits

Offline enumeration currently supports **k=0 collinear equal-magnitude families
only**. Nonzero propagation enumeration raises `NotImplementedError`; there is
no network or known-answer fallback. Stabilizer subspaces allow continuous
directions, but are not a complete irreducible-representation search.
Noncollinear, multiple-independent-amplitude and incommensurate models
are unsupported. Real powder data can leave orientations or groups degenerate.
Difference peaks are magnetic-sensitive, not proof of magnetic order: structural
transitions, impurities and thermal displacement changes can also produce them.
Reports retain these qualifications and explicitly compare nuclear-only fits.
MCIF symmetry is reidentified from **refined** Cartesian axial moments and
validated operation-by-operation; operations use the original parent setting.
Final MCIF includes nonmagnetic species and occupancy/ADP information.

## Generic synthetic tests

Authoritative native integration/scattering controls:

```bash
PYTHONPATH=tools/GSAS-II-master OPENBLAS_NUM_THREADS=1 \
  /path/to/gsas-python -m unittest magnetic_analysis.test_native
```

These cover independent magnetic orbit moments, full-composition export,
native coordinate/origin/metric coupling, supercell scale normalization,
network-error/no-fallback behavior, generic transport privacy/cache, positive
profile bounds and a real generated high/low Rietveld fit.

```sh
PYTHONPATH=tools/GSAS-II-master \
 /home/wangh/software/anaconda3/envs/gsas2_py311/bin/python \
 -m unittest magnetic_analysis.test_engine -q
```

Tests create synthetic CIF/TOF files inside UUID-specific directories in this
package and remove only their own directories, allowing concurrent runs.
They cover nonorthogonal moment normalization, signed modes, absent magnetic
signal, scale/alignment recovery, anchor failures, bounds, ambiguous instrument
matching, nonzero-k rejection, local symmetry validation, MCIF preservation and
real GSAS-II refinement of an independently generated magnetic TOF profile.
One coupled GSAS-II source repair is required: the magnetic symmetry print-table
append in `GSASIIstrIO.GetPhaseData` must execute on successful spin lookup,
not only in its exception branch (otherwise every magnetic refinement crashes).
