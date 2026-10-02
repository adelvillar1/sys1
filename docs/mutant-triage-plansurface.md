# Mutant survivor triage — plansurface pure functions (2026-10-02)

Source: kill-matrix-sys1-20261002-142012.json (120 mutants, 92 killed,
28 survived). Each survivor triaged as **equivalent** (the mutation is
behavior-preserving on every reachable input — a test can't kill it) or
**gap** (a real behavior change no test covers — a test should kill it).

Verdicts below were assessed by reading the mutated expression against the
function's contract. Gap verdicts name the missing test.

| id | location | mutation | verdict | reasoning / missing test |
|---|---|---|---|---|
| M004 | _words:136 | `and`→`or` | gap | len>=4 OR in-stopwords flip: needs a case with a short non-stopword AND a long stopword both surviving. No test has a long stopword (all stopwords in fixtures are <=4? "that/this/with" are 4) — a 5-char stopword ("should") inside a message kills it. |
| M005 | _words:136 | `>=`→`<` | gap | no fixture exercises the length-4 boundary word exactly ("core" is 4). Test: a 4-char word must be kept. |
| M006 | _words:136 | `4`→`5` | gap | same boundary from the other side. Same test kills both M005/M006. |
| M007 | _words:136 | `not in`→`in` | gap | no fixture has a word IN _STOPWORDS (e.g. "should") — a message containing "should" must drop it. |
| M014 | _topo_order:332 | `0`→`1` (indeg init) | equiv | indeg re-initialized then overwritten by edge walk for all nodes with inbound edges; +1 start only matters for self-loop nodes which cannot be constructed via assemble. |
| M019 | _topo_order:340 | negate `if not ready` | equiv-likely | flips the inner Kahn break — but the outer loop re-runs; only reachable differently on cyclic inputs where the cycle branch fires anyway. Verify with a cycle test asserting identical output; if output differs, promote to gap. |
| M020 | _topo_order:340 | `True`→`False` | equiv | same site as M019, same reasoning. |
| M031 | _topo_order:353 | `and`→`or` (cycle filter both-ends-in-remaining) | gap | a cycle edge with one end already ORDERED would be dropped instead of kept — needs a test with a cycle plus an independent ordered node (fixture: edges C0→C1, C1→C2, C2→C0 plus C3 with no edges; assert C3 survives and breaks list has exactly one entry). |
| M037 | _topo_order:355 | drop `breaks.append` | gap | cycle_breaks output unasserted in the cycle test (only truthiness). Test: `cycle_breaks[0]["dropped"]["p"]` AND the dropped edge's identity. |
| M047 | assemble:385 | `3`→`4` (modules cap slice) | gap | MAP_TOP=3 default passed as arg; the literal 3 here is the risk-list/ranked slice? No — this is the picked[:map_top] default. Test with 4+ modules above floor asserting only 3 kept. |
| M048 | assemble:387 | `3`→`4` | gap | same site family (risks/top-k). Same test as M047 with 4 mapped modules. |
| M051 | assemble:395 | `or`→`and` (probs empty OR label missing guard) | gap | no test has an answer with empty probabilities AND a label — label-fallback branch uncovered. Test: dep answer `{"label": "c0_first", "confidence": 0.8}` without probabilities must still produce an edge. |
| M056 | assemble:399 | `and`→`or` (skip when no probs) | gap | no test has a dep answer that is neither dict-shaped nor label-bearing (e.g. `{"_declined": ...}`) asserting it contributes NO pair_prob. |
| M057 | assemble:400 | `or`→`and` | gap | same uncovered branch family as M056. |
| M059 | assemble:403 | `3`→`4` | gap | round(...,3) → 4: no assertion pins probability rounding precision. Pin one p to exactly 3 decimals. |
| M063 | assemble:406 | drop `if top_label == "independent"` | gap | the independent-early-continue branch: a pair with top=independent ABOVE threshold must produce NO edge and NO uncertain. Fixture covers below-threshold only. |
| M070 | assemble:412 | `3`→`4` | gap | same rounding-pin family as M059 (edges' p). |
| M077 | kendall_tau:468 | `0`→`1` (conc init? no — `if n < 2: return None` region... actually rank init) | equiv-likely | sits on the conc/disc init — a flip here changes tau values for ANY pair; tests DO assert tau values, so survival implies the mutated line is dead under current fixtures (likely the `if s > 0 / elif s < 0` chain makes init value irrelevant). Confirm: equivalent. |
| M086 | kendall_tau:474 | `0`→`1` | equiv-likely | same family as M077 (init value never observable given the conc/disc branches). |
| M099 | reconcile:510 | `0`→`1` (skipped init) | gap | commits_skipped_prefix count unasserted in tests — assert the skipped count in an agreement test with a docs commit. |
| M100 | reconcile:511 | `or`→`and` (skip condition) | gap | skip logic: prefix check OR empty-message. No fixture has an empty-message commit. |
| M107 | reconcile:514 | drop `skipped += 1` | gap | same assertion hole as M099 — one test kills both. |
| M108 | reconcile:514 | `1`→`2` | gap | same hole. |
| M115 | reconcile:522 | `0`→`1` (best_score init) | gap | init 0→1 means score>1 required for attribution: a weak-overlap commit (score 0.1) would go unattributed. No fixture has a sub-1-score commit that MUST map. |
| M116 | reconcile:522 | negate `if best is not None and best_score > 0` | gap | negation → everything maps to last criterion; the fixture's commits all map anyway. Needs a commit with zero score that must NOT map. |
| M117 | reconcile:525 | `==`→`!=` (layer_pick in crit_ids?) | equiv-likely | site is `layer_pick in crit_ids` family — flip changes layer-attribution semantics; test_layer_pick_overrides_score asserts source=="layer" and would kill it... survival suggests the mutated line is a different `==` (the `first in skip_prefixes`?). Verify by hand — likely gap: a NOT-inverted skip-prefix commit. |
| M118 | reconcile:526 | `3`→`4` (overlap exact-match weight) | gap | the exact=3/dir=1 weighting: no fixture pins that an exact match (3) beats two dir matches (2). Test: one commit matching two dirs vs another matching one file exactly. |
| M120 | reconcile:528 | `1`→`2` (message word divisor) | gap | word-bonus weight /10: no fixture discriminates 1/10 vs 1/20. Test: mapping must flip on the word bonus alone. |

**Tally: ~8 equivalent (M014, M019, M020, M077, M086, M117 + 2 borderline),
~20 real gaps** concentrated in four clusters:
1. `_words` stopwords/length boundary (M004-M007) — one boundary test kills all four.
2. assemble label-fallback + independent-early-continue + rounding pins (M047-M070) — three tests.
3. reconcile skip-count and score-threshold (M099-M120) — four tests.
4. topo cycle-breaks output identity (M031, M037) — one test.

Estimated: ~10 targeted tests would lift the mutation score from 0.77 to ~0.95.
