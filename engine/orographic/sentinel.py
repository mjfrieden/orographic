from __future__ import annotations

import json
import urllib.request
import urllib.error
import yfinance as yf
from dataclasses import dataclass

@dataclass
class SentinelScore:
    multiplier: float
    catalyst: str
    rationale: str

def fetch_ai_multiplier(symbol: str) -> SentinelScore:
    """
    Fetches the top 3 headlines for a symbol, routes them to the Cloudflare AI Sentinel edge route,
    and returns an asymmetric edge multiplier. Gracefully degrades to 1.0 (neutral) if anything fails.
    """
    default_score = SentinelScore(multiplier=1.0, catalyst="none", rationale="No AI intelligence gathered.")
    
    try:
        # 1. Grab breaking news from yfinance
        news_items = yf.Ticker(symbol).news
        if not news_items:
            return default_score
            
        # Extract the titles of the top 3 most recent news items
        headlines = []
        for item in news_items[:3]:
            title = item.get("title") or item.get("content", {}).get("title")
            if title:
                headlines.append(title)
                
        if not headlines:
            return default_score

        # 2. Dispatch to Cloudflare Workers AI local/remote endpoint
        url = "http://127.0.0.1:8792/api/ai/sentinel"
        payload = json.dumps({"symbol": symbol, "headlines": headlines}).encode("utf-8")
        req = urllib.request.Request(
            url, 
            data=payload, 
            headers={"Content-Type": "application/json"}
        )

        with urllib.request.urlopen(req, timeout=3.0) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("ok"):
                return SentinelScore(
                    multiplier=float(data.get("multiplier", 1.0)),
                    catalyst=data.get("catalyst", "none"),
                    rationale=data.get("rationale", "")
                )
    except Exception:
        # Graceful degradation ensures execution engine never halts
        pass
        
    return default_score
