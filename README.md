# Genius Annotation Analytics Dashboard

A dashboard that pulls annotation open events from Mixpanel, cross-references them with the Genius API, and displays top lyrics and annotations by artist over the past 30 days.

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in your credentials:
   ```
   cp .env.example .env
   ```

3. Run the dashboard:
   ```
   python app.py
   ```

4. Open http://localhost:5000 in your browser.

## How It Works

1. **Mixpanel Export** - Fetches `song:open_annotation` events from the last 30 days via the Mixpanel Raw Data Export API
2. **Annotation Enrichment** - Extracts annotation IDs from events and fetches metadata (lyrics, song, artist) from the Genius API
3. **Dashboard** - Aggregates and displays the data with:
   - Summary stats (total opens, unique annotations, artists, avg opens)
   - Bar chart of top artists by annotation opens
   - Doughnut chart of top songs
   - Sortable, searchable artist leaderboard
   - Sortable, searchable lyrics & annotations table

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Dashboard UI |
| `GET /api/data` | JSON data (cached 10 min) |
| `GET /api/refresh` | Force-refresh data |

## Project Structure

```
app.py               - Flask web server + API endpoints
config.py            - Credentials and configuration
mixpanel_client.py   - Mixpanel data export integration
genius_client.py     - Genius API integration
templates/
  dashboard.html     - Interactive dashboard UI
```
