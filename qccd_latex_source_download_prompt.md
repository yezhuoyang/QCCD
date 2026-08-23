# Prompt: collect LaTeX sources for the QCCD / trapped-ion architecture corpus

Copy the prompt below into a code-capable research agent with internet access. It is intentionally strict about provenance, rate limiting, secure archive extraction, and papers that do not have public source packages.

---

You are a research-software agent. Build a reproducible local corpus of **publicly available author-provided LaTeX sources** for papers relevant to:

1. QCCD and shuttling-based trapped-ion hardware architecture;
2. finite-capacity traps, blocking/roadblocks, junctions, vacancies, ion reordering, transport primitives, cooling, and electrode/control wiring;
3. architecture description, hardware-control ISA/semantics, QASM-to-native compilation, mapping, routing, scheduling, exact solvers, visualization, and simulation;
4. QEC-aware QCCD codesign, especially bivariate-bicycle (BB) and hypergraph-product (HGP) codes;
5. the BB `[[144,12,12]]` / Gross-code architecture question;
6. code + architecture choices for a trapped-ion logical-memory or application breakeven demonstration.

## Non-negotiable rules

- Download only lawful, publicly offered source archives, primarily from arXiv’s official e-print/source endpoint. Do not scrape paywalls or attempt to bypass access controls.
- A PDF is not LaTeX source. If source is unavailable, record the paper in `source_unavailable.csv` with its DOI/official URL and continue.
- Preserve provenance, versions, timestamps, hashes, licenses when exposed, and retrieval errors.
- Be polite to arXiv: identify the client with a descriptive User-Agent/contact, perform at most one request every 3 seconds, use exponential backoff with jitter for 429/5xx, and resume rather than restarting.
- Never blindly extract an archive. Reject absolute paths, `..` traversal, device files, FIFOs, hard links, and symbolic links. Extract only regular files/directories beneath the paper’s destination. Impose sensible maximum archive and expanded sizes.
- Never execute code from a downloaded source tree.
- Do not silently omit a seed. Every seed must end in `downloaded`, `no_public_source`, `metadata_only`, or `failed`, with a reason.
- Do not claim the corpus is mathematically exhaustive. Report search queries, databases, dates, and stopping criteria.

## Output layout

Create:

```text
qccd_paper_sources/
  README.md
  manifest.csv
  manifest.json
  all.bib
  search_log.md
  source_unavailable.csv
  failed.csv
  archives/
    <normalized-arxiv-id>.<detected-extension-or-tar>
  papers/
    <normalized-arxiv-id>__<short-title>/
      metadata.json
      SHA256SUMS
      source/
```

The manifest must include: normalized ID, requested version, latest version, title, authors, submitted/updated dates, DOI, journal reference, primary category, abstract URL, source URL, license URL if exposed, retrieval timestamp in UTC, HTTP content type, archive SHA-256, expanded-file count/bytes, status, and error/reason.

## Phase 1 — verify and normalize the seed corpus

Use arXiv metadata to verify every ID/title. Strip URL wrappers and normalize legacy IDs while preserving requested versions. Deduplicate versions but record both the requested and latest version. Do not trust the titles below if metadata disagrees.

### Core QCCD hardware and transport seeds

```text
quant-ph/0508097
quant-ph/0702175
0901.0533
0909.2464
1206.0364
1206.0780
1210.3655
1208.0391
1508.00420
1602.02840
1607.03734
1904.04178
1912.04712
2003.01293
2003.03520
2201.07358
2305.03828
2305.12773
2401.18056
2403.00208
2403.00756
2405.11450
2407.07694
2504.01815
2601.08495
2603.06208
2605.25118
2606.21899
2607.14441
```

### QCCD compiler, routing, scheduler, and codesign seeds

```text
2004.04706
2010.15876
2206.00544
2207.01964
2311.03454
2311.10687
2402.14065
2408.00225
2501.12470
2501.15200
2502.04181
2504.16303
2504.17886
2505.01316
2505.07928
2505.16891
2508.03914
2509.25988
2510.23519
2511.15910
2512.02890
2512.18021
2603.19969
2605.09237
2605.22463
2607.24714
```

### BB/HGP, syndrome extraction, decoder, and breakeven seeds

```text
2109.14599
2109.14609
2307.10147
2308.07915
2308.15520
2404.17676
2406.14527
2407.03973
2407.13826
2408.10001
2409.01440
2503.22071
2504.02673
2506.03094
2508.14227
2508.20858
2511.09683
2602.22770
2603.05481
2604.19481
2605.04663
2606.06455
2607.27644
```

## Phase 2 — discover missing relevant papers

Search arXiv, Crossref/OpenAlex/Semantic Scholar metadata where allowed, DBLP/IEEE/ACM metadata, and the reference lists of the P0/core papers. Use combinations of:

```text
QCCD trapped ion architecture
trapped-ion shuttling compiler
ion shuttling routing scheduling capacity junction
grid QCCD racetrack grate ring architecture
trapped ion control broadcast SIMD WISE electrode wiring
QCCD QASM compiler simulator
SAT SMT CP-SAT ion shuttling
trapped ion quantum error correction architecture
bivariate bicycle trapped ion QCCD
Gross code [[144,12,12]] trapped ion
hypergraph product QCCD layout
syndrome extraction circuit distance Gross code
trapped ion qLDPC breakeven
```

Perform backward and forward citation snowballing from at least these anchors:

- arXiv:2504.17886 (TrapSIMD / FluxTrap)
- arXiv:2511.15910 (Cyclone)
- arXiv:2501.12470 (Position Graph / SHAPER)
- arXiv:2510.23519 (surface-code QCCD architecture DSE)
- arXiv:2402.14065 and arXiv:2311.03454 (MQT IonShuttler)
- arXiv:2004.04706 (QCCDSim)
- arXiv:2603.05481 (syndrome-extraction circuit distance)
- arXiv:2506.03094 (Tour de Gross)
- arXiv:2604.19481 (Walking Cat)
- arXiv:2606.06455 (qLDPC breakeven)

Include a newly found paper if it directly changes the hardware state model, control constraints, routing/scheduling algorithm, architecture tradeoff, QEC circuit, decoder throughput, or breakeven evaluation. Put broadly generic trapped-ion experiments, unrelated neutral-atom routing, and generic quantum-compiler papers in an “adjacent/rejected” section of `search_log.md` with a one-line reason rather than downloading them.

Stop only after:

1. two consecutive snowball passes add no direct papers;
2. all search query families above have been run with the search date recorded;
3. every P0/core paper’s references have been checked for missed QCCD compiler/hardware work;
4. duplicates, renamed conference versions, and arXiv/journal versions have been reconciled.

## Phase 3 — retrieve source packages

For each verified arXiv paper:

1. Query official metadata and select the requested/latest version according to a documented policy.
2. Retrieve the official source/e-print archive (for example, the link exposed as “TeX Source” on the arXiv abstract page or the documented e-print endpoint).
3. Follow redirects only within expected arXiv domains.
4. Validate status, content type, magic bytes, and size. Detect a single TeX file, gzip, tar, or compressed tar. An HTML error page or PDF response is not a successful source download.
5. Save the original response archive before extraction and compute SHA-256.
6. Safely extract into a fresh per-paper directory using the path/link/device restrictions above.
7. Inventory `.tex`, `.bib`, `.sty`, `.cls`, figures, generated tables/data, and build files. Do not fetch dependencies referenced by the source.
8. Identify likely root TeX files without executing them; record the heuristic in metadata.
9. Generate per-paper checksums.
10. Update manifests atomically so the job can resume after interruption.

If an arXiv record provides only PDF or lacks source, record `no_public_source`. If an archive is malformed or unsafe, quarantine the original archive, record `failed_unsafe_archive`, and do not extract it.

## Phase 4 — non-arXiv/source-unavailable ledger

Verify and record at least these works even if no public LaTeX source exists:

```text
Kielpinski, Monroe, Wineland — Architecture for a large-scale ion-trap quantum computer — Nature 417 (2002)
Saki et al. — Muzzle the Shuttle: Efficient Compilation for Multi-Trap Trapped-Ion Quantum Computers — DATE 2022
Tseng et al. — SMT-Based Qubit Mapping for Trapped-Ion Quantum Computing — ISPD 2024
Dai et al. — Advanced Shuttle Strategies for Parallel QCCD Architectures — IEEE TQE 2024 — DOI 10.1109/TQE.2024.3408757
Schoenberger et al. — Using Compiler Frameworks for the Evaluation of Hardware Design Choices — QCE 2024
Schoenberger et al. — Shuttling for Trapped-Ion Quantum Computers with Embedded Processing Zones — QSW 2025
```

Look for a lawful author manuscript or institutional repository only through ordinary public links. Do not infer that a PDF implies source availability. Record official paper URL, DOI, bibliographic metadata, public manuscript URL if any, and `latex_source_available=false` unless an author-provided source archive is actually found.

## Phase 5 — quality checks and report

- Verify that each successful source directory contains at least one plausible TeX/source file.
- Detect duplicate archives and duplicate paper versions by hashes and metadata.
- Produce `all.bib` from authoritative metadata, with stable citation keys.
- Cross-check that all seed IDs occur exactly once in the manifest.
- Summarize counts by status and priority.
- List any metadata/title correction made to the seed list.
- List newly discovered direct papers separately from the original seeds.
- In `README.md`, include exact commands and environment versions needed to rerun the downloader, but do not include credentials.
- End with a short research map grouping papers into hardware/control, general compilation, exact scheduling, QEC codesign, BB/HGP, circuit-distance/decoder, and breakeven.

Finally, print the corpus root, total papers considered, source archives successfully downloaded, source-unavailable count, failures, unsafe archives rejected, and the paths to `manifest.csv`, `all.bib`, and `search_log.md`.

---

## Notes for the operator

- Set a real contact address in the downloader’s User-Agent if arXiv’s current guidance requests it.
- The prompt asks the agent to verify current endpoint rules instead of hard-coding an undocumented scraping behavior.
- Re-running should be idempotent: unchanged verified archives must not be downloaded again.
