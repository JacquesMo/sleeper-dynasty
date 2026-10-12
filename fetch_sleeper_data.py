"""
Sleeper Fantasy Football Dynasty Data Collector
==============================================
Pulls all user, league, roster, draft, transaction, and player data from the Sleeper API
and organizes it into structured JSON files in the `data/` directory.

You can run this directly with no terminal arguments:
    python fetch_sleeper_data.py
Or modify the CONFIGURATION block below.
"""

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path
import requests

# Ensure UTF-8 output on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ==============================================================================
# CONFIGURATION SETTINGS (Edit parameters directly here)
# ==============================================================================
USERNAME = "jacques"              # Your Sleeper username
LEAGUE_NAME_OR_ID = "Farhiless"   # League name (e.g. "Farhiless") or exact League ID (e.g. "1202721452562841600")
SKIP_PLAYERS_DB = False           # Set True to skip downloading players.json if already cached
# ==============================================================================

BASE_URL = "https://api.sleeper.app/v1"
DATA_DIR = Path("data")


def log(msg, symbol="ℹ️"):
    print(f"[{symbol}] {msg}")


def api_get(endpoint, params=None, retries=3, delay=0.2):
    """Safe GET request to Sleeper API with retry logic and gentle rate limiting."""
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    time.sleep(delay)
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                return None
            else:
                log(f"HTTP {resp.status_code} for {url} (attempt {attempt + 1}/{retries})", "⚠️")
        except requests.RequestException as e:
            log(f"Request error: {e} (attempt {attempt + 1}/{retries})", "⚠️")
        time.sleep(1)
    return None


def save_json(filepath, data):
    """Write dictionary or list to JSON file with indentation."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_user_info(username_or_id):
    """Fetch user profile by username or ID."""
    log(f"Looking up Sleeper user: '{username_or_id}'...", "🔍")
    user_data = api_get(f"user/{username_or_id}")
    if not user_data or "user_id" not in user_data:
        return None
    return user_data


def get_user_leagues(user_id):
    """Fetch leagues for a user across recent seasons to find active leagues."""
    current_year = datetime.datetime.now().year
    seasons_to_check = [current_year, current_year - 1, current_year - 2, current_year + 1]
    
    all_leagues = []
    seen_league_ids = set()
    
    for yr in seasons_to_check:
        log(f"Checking for leagues in {yr}...", "📅")
        leagues = api_get(f"user/{user_id}/leagues/nfl/{yr}")
        if leagues:
            for lg in leagues:
                lid = lg.get("league_id")
                if lid and lid not in seen_league_ids:
                    seen_league_ids.add(lid)
                    all_leagues.append(lg)
    return all_leagues


def find_matching_league(leagues, search_term):
    """Finds a league by exact ID or case-insensitive name match."""
    # Check if exact league ID
    for lg in leagues:
        if lg.get("league_id") == search_term:
            return lg

    # Search by name (return the latest season if multiple matches)
    term_lower = search_term.lower()
    matches = [lg for lg in leagues if term_lower in lg.get("name", "").lower()]
    if matches:
        # Sort by season descending to pick the newest active season
        matches.sort(key=lambda x: str(x.get("season", "0")), reverse=True)
        return matches[0]
    return None


def get_complete_league_history(start_league_id):
    """
    Traverses the historical dynasty chain using `previous_league_id`
    from the current season back to inception.
    Returns a chronologically ordered list (oldest season to newest).
    """
    history_chain = []
    current_id = start_league_id
    visited = set()
    
    log(f"Tracing league history chain starting from ID: {start_league_id}...", "🔗")
    
    while current_id and current_id not in visited:
        visited.add(current_id)
        league_info = api_get(f"league/{current_id}")
        if not league_info:
            log(f"Could not load league metadata for ID {current_id}", "⚠️")
            break
        
        season = league_info.get("season", "unknown")
        name = league_info.get("name", "Unnamed League")
        prev_id = league_info.get("previous_league_id")
        
        log(f"Found Season {season}: '{name}' (ID: {current_id})", "🏆")
        history_chain.append(league_info)
        
        if not prev_id or prev_id == "0":
            log(f"Inaugural season reached: {season}!", "🎉")
            break
        current_id = prev_id

    # Reverse to have oldest -> newest
    history_chain.reverse()
    return history_chain


def download_players_db(force=False):
    """
    Downloads full Sleeper NFL players database (IDs -> Names/Positions/Teams).
    Cached in data/players.json to avoid re-downloading (~30MB).
    """
    players_file = DATA_DIR / "players.json"
    if players_file.exists() and not force:
        log("Player database already cached at data/players.json. Skipping download.", "💾")
        return players_file

    log("Downloading complete Sleeper NFL player database (~30MB)...", "📥")
    players_data = api_get("players/nfl", delay=0.5)
    if players_data:
        save_json(players_file, players_data)
        log(f"Saved {len(players_data)} players to {players_file}", "✅")
    else:
        log("Failed to download players database.", "❌")
    return players_file


def clean_roster_data(rosters):
    """Removes all nickname metadata from rosters to keep data strictly factual."""
    cleaned = []
    for r in rosters:
        r_copy = dict(r)
        if isinstance(r_copy.get("metadata"), dict):
            # Strip all p_nick_* keys
            clean_meta = {k: v for k, v in r_copy["metadata"].items() if not k.startswith("p_nick_")}
            r_copy["metadata"] = clean_meta
        cleaned.append(r_copy)
    return cleaned


def fetch_all_league_data(league_chain, target_dir):
    """
    For each season in the dynasty chain, downloads:
    - league metadata
    - users (managers)
    - rosters (with nicknames stripped)
    - drafts & draft picks
    - traded draft picks
    - transactions (trades, waivers, free agency for weeks 1-18)
    """
    summary = []
    
    for league in league_chain:
        league_id = league["league_id"]
        season = league.get("season", "unknown")
        season_dir = target_dir / "seasons" / str(season)
        season_dir.mkdir(parents=True, exist_ok=True)
        
        log(f"\n--- Gathering Data for Season {season} (League ID: {league_id}) ---", "📊")
        
        # 1. League Info
        save_json(season_dir / "league.json", league)
        
        # 2. Users / Managers
        log("Fetching managers...", "👥")
        users = api_get(f"league/{league_id}/users") or []
        save_json(season_dir / "users.json", users)
        
        # 3. Rosters (Nicknames removed)
        log("Fetching rosters (cleaned without nicknames)...", "📋")
        raw_rosters = api_get(f"league/{league_id}/rosters") or []
        rosters = clean_roster_data(raw_rosters)
        save_json(season_dir / "rosters.json", rosters)
        
        # 4. Traded Draft Picks
        log("Fetching traded future picks...", "🔄")
        traded_picks = api_get(f"league/{league_id}/traded_picks") or []
        save_json(season_dir / "traded_picks.json", traded_picks)
        
        # 5. Drafts & Draft Picks
        log("Fetching draft history...", "🎯")
        drafts = api_get(f"league/{league_id}/drafts") or []
        save_json(season_dir / "drafts.json", drafts)
        
        all_draft_picks = []
        for draft in drafts:
            draft_id = draft.get("draft_id")
            if draft_id:
                picks = api_get(f"draft/{draft_id}/picks") or []
                all_draft_picks.extend(picks)
        save_json(season_dir / "draft_picks.json", all_draft_picks)
        
        # 6. Transactions (Weeks 1 to 18)
        log("Fetching transactions (trades, waivers, free agent adds/drops)...", "💼")
        all_transactions = []
        for week in range(1, 19):
            txs = api_get(f"league/{league_id}/transactions/{week}", delay=0.1)
            if txs:
                for tx in txs:
                    tx["week"] = week
                    all_transactions.append(tx)
        
        all_transactions.sort(key=lambda x: x.get("created", 0))
        save_json(season_dir / "transactions.json", all_transactions)
        log(f"Saved {len(all_transactions)} transactions for season {season}.", "✅")
        
        summary.append({
            "season": season,
            "league_id": league_id,
            "name": league.get("name"),
            "total_managers": len(users),
            "total_rosters": len(rosters),
            "total_draft_picks": len(all_draft_picks),
            "total_transactions": len(all_transactions)
        })
        
    save_json(target_dir / "history_summary.json", summary)
    log(f"All {len(league_chain)} seasons processed and saved!", "🎉")


def main():
    parser = argparse.ArgumentParser(description="Gather complete Sleeper Fantasy Dynasty League API data")
    parser.add_argument("--username", default=USERNAME, help="Sleeper username (defaults to code config)")
    parser.add_argument("--league", default=LEAGUE_NAME_OR_ID, help="League name or ID (defaults to code config)")
    parser.add_argument("--skip_players", action="store_true", default=SKIP_PLAYERS_DB, help="Skip downloading players.json")
    args = parser.parse_args()

    active_username = args.username or USERNAME
    active_league_param = args.league or LEAGUE_NAME_OR_ID

    print("=" * 65)
    print("SLEEPER DYNASTY DATA COLLECTOR")
    print(f"Target User   : {active_username}")
    print(f"Target League : {active_league_param}")
    print("=" * 65)

    # 1. Fetch user info
    user_info = get_user_info(active_username)
    if not user_info:
        print(f"❌ User '{active_username}' was not found on Sleeper.")
        sys.exit(1)
    save_json(DATA_DIR / "user.json", user_info)
    log(f"Found User: {user_info.get('display_name')} (ID: {user_info.get('user_id')})", "✅")

    # 2. Identify target league
    user_id = user_info["user_id"]
    target_league_id = None

    if active_league_param.isdigit():
        target_league_id = active_league_param
    else:
        leagues = get_user_leagues(user_id)
        if not leagues:
            print(f"❌ No leagues found for user {user_info.get('display_name')}.")
            sys.exit(1)
        
        matched = find_matching_league(leagues, active_league_param)
        if matched:
            target_league_id = matched["league_id"]
            log(f"Matched League: '{matched.get('name')}' (Season: {matched.get('season')}, ID: {target_league_id})", "🎯")
        else:
            print(f"❌ Could not find a league matching '{active_league_param}'.")
            print("Available leagues:")
            for lg in leagues:
                print(f"  - {lg.get('name')} ({lg.get('season')}) [ID: {lg.get('league_id')}]")
            sys.exit(1)

    # 3. Download NFL players database
    if not args.skip_players:
        download_players_db()

    # 4. Trace dynasty chain and download all data
    league_chain = get_complete_league_history(target_league_id)
    if not league_chain:
        print(f"❌ Could not retrieve league history for {target_league_id}.")
        sys.exit(1)

    league_folder = DATA_DIR / f"league_{target_league_id}"
    fetch_all_league_data(league_chain, league_folder)

    print("\n" + "=" * 65)
    print("DATA COLLECTION COMPLETE!")
    print(f"Raw API data saved in: {league_folder.resolve()}")
    print("=" * 65)


if __name__ == "__main__":
    main()
