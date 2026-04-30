
# Genius A&R Signing Tool — Architecture

```
+=====================================================================+
|                         USER (Browser)                               |
|  http://localhost:5000                                                |
+=====================================================================+
        |                    |                    |
        v                    v                    v
+---------------+  +------------------+  +-------------------+
| Signing Board |  |   Global Top     |  |  Artist Detail    |
|   (default)   |  |                  |  |                   |
| Filter by:    |  | Top artists by   |  | Charts: trends,   |
|  - min/max    |  |  annotation      |  |  geo, referrers   |
|    views      |  |  opens from      |  | Annotation table  |
|  - quadrant   |  |  data_cache.json |  | 2x2 Quadrant      |
|  - classified |  |                  |  |  Matrix plot      |
| "Classify"    |  |                  |  | Deal rec           |
|  button       |  |                  |  |                   |
+-------+-------+  +--------+---------+  +--------+----------+
        |                    |                     |
        v                    v                     v
+=====================================================================+
|                     Flask App  (app.py)                               |
|  Port 5000                                                           |
+=====================================================================+
|                                                                      |
|  ROUTES:                                                             |
|                                                                      |
|  GET  /                        --> dashboard.html                    |
|  GET  /api/signing-board       --> JOIN universe + classifications   |
|  POST /api/classify-batch      --> background thread: classify N     |
|  GET  /api/classify-batch/status --> poll progress                   |
|  GET  /api/search?artist=X     --> live Mixpanel query + classify    |
|  GET  /api/data                --> cached global data                |
|  POST /api/enrich              --> background Genius fetch           |
|  GET  /api/annotation/<id>     --> single Genius annotation          |
|  GET  /api/classify?vpu=&aor=  --> standalone classifier             |
|  GET  /api/quadrants           --> framework definitions             |
|                                                                      |
|  CORE LOGIC:                                                         |
|                                                                      |
|  _aggregate_artist_data(artist, days)                                |
|    |                                                                 |
|    +--> _query_mixpanel_artist(artist, days, "song:load")            |
|    |      Returns: page_views[], distinct_ids, songs                 |
|    |                                                                 |
|    +--> _query_mixpanel_artist(artist, days, "song:open_annotation") |
|    |      Returns: annotation_events[]                               |
|    |                                                                 |
|    +--> Computes:                                                    |
|           VPU  = total_views / unique_users                          |
|           SPU  = distinct_songs_per_user (mean)                      |
|           AOR  = annotation_opens / page_views                       |
|                                                                      |
|  _classify(vpu, aor)                                                 |
|    |                                                                 |
|    +--> VPU >= 1.8 && AOR >= 0.15  -->  Q1: Full Catalog Fanbase    |
|    +--> VPU <  1.8 && AOR >= 0.15  -->  Q2: Single Deep Cut         |
|    +--> VPU >= 1.8 && AOR <  0.15  -->  Q3: Lyrics Utility          |
|    +--> VPU <  1.8 && AOR <  0.15  -->  Q4: One-Hit Lookup          |
|                                                                      |
+=====================================================================+
        |                              |
        v                              v
+------------------+     +-------------------------+
| Mixpanel         |     | Genius API              |
| Data Export API  |     | api.genius.com          |
+------------------+     +-------------------------+
|                  |     |                         |
| Raw Export:      |     | OAuth client_credentials|
| data.mixpanel.   |     |                         |
|  com/api/2.0/    |     | GET /annotations/{id}   |
|  export          |     |   -> lyric_fragment     |
|                  |     |   -> annotation_text    |
| Segmentation:    |     |   -> song_title         |
| mixpanel.com/    |     |   -> votes_total        |
|  api/2.0/        |     |   -> verified           |
|  segmentation    |     |                         |
+------------------+     +-------------------------+


+=====================================================================+
|                      LOCAL DATA FILES                                 |
+=====================================================================+
|                                                                      |
|  artist_universe.json          <-- build_universe.py                 |
|  {                                                                   |
|    built_at, days,                                                   |
|    artists: [                  One-time Mixpanel segmentation query   |
|      {artist_name, views_90d}  (3 x 30-day chunks to avoid timeout) |
|    ]                           Every artist with >= N views           |
|  }                                                                   |
|                                                                      |
|  classifications.json          <-- classify_all.py / /api/classify   |
|  {                                                                   |
|    classified_at, days,                                              |
|    artists: [                  Built incrementally: batch or on-demand|
|      {artist, quadrant,                                              |
|       vpu, spu, aor,                                                 |
|       unique_users,                                                  |
|       total_page_views,                                              |
|       engagement_score,        = VPU x AOR                           |
|       deal, thesis,                                                  |
|       top_song}                                                      |
|    ]                                                                 |
|  }                                                                   |
|                                                                      |
|  data_cache.json               <-- pre-fetched global Mixpanel data  |
|  enriched_cache.json           <-- Genius annotation metadata cache  |
|  .env                          <-- API credentials (not committed)   |
|                                                                      |
+=====================================================================+


+=====================================================================+
|                     OFFLINE SCRIPTS                                   |
+=====================================================================+
|                                                                      |
|  build_universe.py                                                   |
|    Mixpanel segmentation --> artist_universe.json                    |
|    Run once, ~3-5 min                                                |
|    Splits 90d into 30d chunks, retries on 5xx                       |
|                                                                      |
|  classify_all.py                                                     |
|    Iterates artist list --> classifications.json                     |
|    Resumable (checkpoints every 10)                                  |
|    Can also be triggered from dashboard via /api/classify-batch      |
|                                                                      |
|  audit.py                                                            |
|    Class of 2023 cohort analysis (historical, not active)            |
|                                                                      |
+=====================================================================+


+=====================================================================+
|                     DATA FLOW SUMMARY                                |
+=====================================================================+
|                                                                      |
|  ONE-TIME SETUP:                                                     |
|                                                                      |
|  build_universe.py                                                   |
|       |                                                              |
|       v                                                              |
|  artist_universe.json  (who exists at what volume)                   |
|                                                                      |
|  ON-DEMAND (from dashboard):                                         |
|                                                                      |
|  User sets filters --> Apply --> /api/signing-board                  |
|       |                                                              |
|       v                                                              |
|  Shows candidates (classified + unclassified)                        |
|       |                                                              |
|       v                                                              |
|  User clicks "Classify N artists"                                    |
|       |                                                              |
|       v                                                              |
|  /api/classify-batch --> background thread                           |
|       |                                                              |
|       +-- For each artist:                                           |
|       |     query Mixpanel (song:load + song:open_annotation)        |
|       |     compute VPU, SPU, AOR                                    |
|       |     _classify(vpu, aor) --> quadrant                         |
|       |     append to classifications.json                           |
|       |                                                              |
|       v                                                              |
|  Results populate in Signing Board table                             |
|  Sorted by engagement_score (VPU x AOR) descending                  |
|  Small artists with superfan behavior float to top                   |
|                                                                      |
+=====================================================================+


+=====================================================================+
|                     2x2 QUADRANT MATRIX                              |
+=====================================================================+
|                                                                      |
|               Low VPU (<1.8)     |     High VPU (>=1.8)             |
|  --------------------------------+--------------------------------   |
|  High AOR  | Q2: Single Deep Cut | Q1: Full Catalog Fanbase        |
|  (>=0.15)  | Sync / EP deal      | SIGN: 360 / catalog deal        |
|            | 1 viral song drives | Deep fans across catalog         |
|            | intense engagement  | Highest priority                 |
|  ----------+---------------------+---------------------------------  |
|  Low AOR   | Q4: One-Hit Lookup  | Q3: Lyrics Utility              |
|  (<0.15)   | PASS                | Distribution / ad-rev deal       |
|            | Drive-by lyric      | High traffic, transactional      |
|            | searches            | (K-pop, worship, dense rap)      |
|  --------------------------------+--------------------------------   |
|                                                                      |
|  Engagement Score = VPU x AOR (higher = more signing signal)         |
|                                                                      |
+=====================================================================+
```
