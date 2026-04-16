# The VPU × AOR Quadrant Framework

**A Shadow A&R Signing Model for Genius**

Window analyzed: H2 2024 – Q1 2026 (post `Primary Artist` instrumentation)
Cohort: 2,742 non-legacy artists with ≥ 1,000 `song:load` events and non-trivial catalog breadth

---

## 1. What Went Wrong With "AOR Predicts Crashes"

The original thesis was that **Annotation Open Rate (AOR)** — the ratio of `song:open_annotation` events to `song:load` events — would predict which breakout artists were about to fade. High AOR was supposed to be a proxy for hardcore fandom; low AOR was supposed to be a lookup crowd.

**It didn't hold up.** In the data-driven H2 2024 cohort:

- At the 100K+ page-view tier, **high-AOR songs retained only 0.41× as well** as low-AOR songs over the following 12 months.
- Specific counter-examples were decisive. **GloRilla** has a low AOR but is commercially durable; the data framed her as a pass, which is obviously wrong. Meanwhile high-AOR artists included niche acts whose audiences simply churned.

AOR on its own conflates two very different behaviors: *fans doing deep engagement* vs. *confused lookup traffic on a single dense song*. We needed a second axis.

## 2. Adding the Second Axis: VPU

**Views Per User (VPU)** — total `song:load` events divided by unique `distinct_id`s — measures how much of an artist's catalog the average visitor consumes. Combined with AOR:

| | **Low AOR** | **High AOR** |
|---|---|---|
| **High VPU** | Q3: Lyrics Utility | Q1: Full Catalog Fanbase |
| **Low VPU** | Q4: One-Hit Lookup | Q2: Single Deep Cut |

Thresholds (fit to the observed distribution of the 2,742-artist cohort):

- **VPU = 1.8** (median split weighted toward catalog breadth)
- **AOR = 0.15** (15% of page views open at least one annotation)

### Why SPU too?

**Songs Per User (SPU)** — distinct song titles per unique user — distinguishes Q3 (Lyrics Utility) from Q1 (Full Catalog Fanbase). Both are high-VPU, but:

- Q1 SPU is typically **> 2.5** (users exploring multiple songs deeply)
- Q3 SPU is typically **1.2–1.8** (same user reloading one lyrics page repeatedly)

SPU is surfaced in the dashboard as a tie-breaker, not a classifier.

## 3. The Four Quadrants

### Q1 — Full Catalog Fanbase
**580 artists. The shortlist.**

Deep users **and** heavy annotation engagement. Fans are consuming across the catalog and reading context on every song. This is the textbook signing target.

Representative: Melanie Martinez, A$AP Rocky, Mitski, Noah Kahan.

**Recommended deal:** Full artist / 360 / catalog partnership. Premium editorial investment. Expect to beat 80% of the cohort on 12-month retention.

### Q2 — Single Deep Cut
**666 artists.**

Shallow traffic, high AOR. One song is driving intense lyric scrutiny — a moment, a viral lyric, a TikTok trigger. The user base is not yet broad but the engagement on that single surface is real.

Representative: Sabrina Carpenter (pre-"Espresso" breakout pattern), Billie Eilish (certain album cycles), Chappell Roan.

**Recommended deal:** Sync partnership, single-song licensing, or EP-scoped deal. Do **not** pay catalog pricing. Re-evaluate at next album cycle — many of these graduate to Q1, but most stay here.

### Q3 — Lyrics Utility
**167 artists.**

High VPU, low AOR. Traffic is deep but transactional: people are treating the artist's Genius pages as a lyrics reference, not reading context. Often translation-driven or lyric-density-driven (K-pop, worship, rap with dense wordplay where users scroll but don't click).

Representative: BTS, BLACKPINK, broader K-pop roster.

**Recommended deal:** Distribution / ad-revenue share. Monetize the traffic, don't invest in editorial depth the audience isn't consuming.

### Q4 — One-Hit Lookup
**1,329 artists. The large middle.**

Shallow traffic, no annotation engagement. Drive-by lyric searches. Almost half the cohort lives here.

Representative: One Direction (catalog-era), most worship music, most legacy one-hit entries.

**Recommended deal:** Pass on artist-level investment. Roll into playlist/utility monetization.

## 4. Why This Framework Moves The Needle

Three reasons the quadrant model is actionable in a way single-metric AOR wasn't:

1. **It matches deal type to behavior.** The failure mode of the AOR-only model was recommending a full catalog deal to a Q2 artist whose audience will evaporate after the TikTok moment. The quadrant says: do an EP deal instead.
2. **It reclassifies counter-examples.** GloRilla scores Q3/Q4-ish on AOR alone but her VPU is healthy — she's a Q3 (utility) candidate for a distribution deal, not a pass. The model no longer says "no" to obvious successes.
3. **It prices itself.** Each quadrant corresponds to a commercial structure Genius already uses. There's no new deal machinery required, just a routing rule.

## 5. Known Limitations

- **VPU ignores time-on-page.** A user who loads 5 songs in 30 seconds looks the same as one who stays an hour.
- **Thresholds are fit, not proven.** The 1.8 / 0.15 cuts matched the observed distribution; they have not been validated against a held-out cohort's 12-month retention.
- **No streaming cross-check yet.** The framework is internally consistent but we haven't yet paired it with Spotify monthly-listener trajectories to confirm that Q1 classification correlates with commercial durability.
- **"Primary Artist" only exists from May 2024 on.** No pre-2024 baseline is possible in Mixpanel.

## 6. What's In The Dashboard

The Flask app at `/` now returns a `classification` block on `/api/search?artist=X&days=N`:

```json
{
  "quadrant": "Q1",
  "name": "Full Catalog Fanbase",
  "thesis": "...",
  "deal": "...",
  "vpu": 2.41,
  "spu": 3.10,
  "aor": 0.22,
  "unique_users": 48210
}
```

Additional endpoints:

- `GET /api/classify?vpu=...&aor=...` — classify arbitrary inputs
- `GET /api/quadrants` — return the full framework definition

The artist search page renders a 2×2 matrix with the artist's position plotted, the quadrant highlighted, and the recommended deal structure shown inline.

## 7. Next Experiments

1. **AOR duration, not magnitude.** Does an artist who *sustains* a high AOR for 6+ months retain better than one who spikes-then-fades? This was the original instinct; it may still be true on the right timeframe.
2. **Streaming cross-check.** Pair quadrant classification with Spotify monthly-listener trajectories. Target: 80% of Q1 should outperform cohort on 12-month streaming retention.
3. **Q2 → Q1 graduation rate.** What fraction of Q2 artists move to Q1 within 12 months? That's the A&R pipeline signal.
