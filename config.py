import os
from dotenv import load_dotenv

load_dotenv()

MIXPANEL_API_SECRET = os.getenv("MIXPANEL_API_SECRET")
GENIUS_CLIENT_ID = os.getenv("GENIUS_CLIENT_ID")
GENIUS_CLIENT_SECRET = os.getenv("GENIUS_CLIENT_SECRET")

MIXPANEL_EXPORT_URL = "https://data.mixpanel.com/api/2.0/export"
GENIUS_API_BASE = "https://api.genius.com"
GENIUS_OAUTH_TOKEN_URL = "https://api.genius.com/oauth/token"

EVENT_NAME = "song:open_annotation"
LOOKBACK_DAYS = 30
