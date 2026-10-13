"""
Sleeper Fantasy Football Dynasty Organizer & Visualizer
======================================================
Organizes and visualizes your complete dynasty team history from 2019 to 2025,
tracking how every player was acquired (Draft, Trade, Waivers, FA) and how the roster evolved.

No terminal parameters needed. Simply configure below and run:
    python organize_team_history.py
"""

import argparse
import datetime
import json
import sys
from collections import defaultdict
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ==============================================================================
# CONFIGURATION PARAMETERS (Edit directly here)
# ==============================================================================
SEASON = 2026               # Target season to view in console: 2019-2026, or "ALL"
VIEW = "MOVEMENT"           # Console view: "ROSTER", "MOVEMENT" (turnover), "TRADES", "TIMELINE", "ALL"
POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"]
GENERATE_HTML = True        # Generates comprehensive visual interactive dashboard team_report.html
# ==============================================================================

DATA_DIR = Path("data")


def load_json(filepath):
    if not filepath.exists():
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def get_user_and_league():
    user = load_json(DATA_DIR / "user.json")
    if not user:
        print("Error: data/user.json not found. Run fetch_sleeper_data.py first.")
        sys.exit(1)
    
    league_dirs = [d for d in DATA_DIR.iterdir() if d.is_dir() and d.name.startswith("league_")]
    if not league_dirs:
        print("Error: No league data found in data/. Run fetch_sleeper_data.py first.")
        sys.exit(1)
    
    # Pick directory with the most seasons (e.g. 2019-2026)
    league_dirs.sort(key=lambda d: len(list((d / "seasons").iterdir())) if (d / "seasons").exists() else 0, reverse=True)
    return user, league_dirs[0]


def build_draft_pick_map(league_dir, season_names, players_db):
    """
    Builds a lookup table mapping (season, round, original_roster_id)
    to the specific player drafted with that pick.
    """
    draft_pick_map = {}
    seasons_dir = league_dir / "seasons"
    
    for s in season_names:
        s_path = seasons_dir / s
        drafts = load_json(s_path / "drafts.json") or []
        if not drafts:
            continue
        draft = drafts[0]
        draft_order = draft.get("draft_order") or {}
        slot_to_uid = {slot: uid for uid, slot in draft_order.items()}
        rosters = load_json(s_path / "rosters.json") or []
        uid_to_rid = {r.get("owner_id"): r.get("roster_id") for r in rosters}
        
        picks = load_json(s_path / "draft_picks.json") or []
        for p in picks:
            rd = p.get("round")
            slot = p.get("draft_slot")
            pick_no = p.get("pick_no")
            pid = p.get("player_id")
            pinfo = players_db.get(pid, {})
            m = p.get("metadata") or {}
            pname = pinfo.get("full_name") or f"{m.get('first_name', '')} {m.get('last_name', '')}".strip() or f"Player {pid}"
            pos = pinfo.get("position") or m.get("position", "")
            
            orig_uid = slot_to_uid.get(slot)
            orig_rid = uid_to_rid.get(orig_uid)
            if orig_rid:
                draft_pick_map[(str(s), rd, orig_rid)] = {
                    "season": s,
                    "round": rd,
                    "pick_no": pick_no,
                    "player_name": pname,
                    "pos": pos,
                    "player_id": pid
                }
    return draft_pick_map


def format_traded_pick(dp, draft_pick_map):
    season = str(dp.get("season"))
    round_no = dp.get("round")
    orig_rid = dp.get("roster_id")
    base = f"{season} Rd {round_no}"
    match = draft_pick_map.get((season, round_no, orig_rid)) if draft_pick_map else None
    if match:
        pos_str = f", {match['pos']}" if match.get("pos") else ""
        return f"{base} (Pick #{match['pick_no']} → {match['player_name']}{pos_str})"
    elif season.isdigit() and int(season) > 2026:
        return f"{base} (Future / Pending Draft)"
    return f"{base} Draft Pick"


def build_full_history(league_dir, uid, players_db, draft_pick_map=None):
    """
    Builds a complete multi-year dataset tracking:
    - User's roster per season
    - Exact acquisition origin for EVERY player (Draft pick, Trade details, Waivers, FA)
    - Year-over-year transitions (Joined, Departed, Retained)
    - All trades executed across all seasons
    - All draft picks made across all seasons
    """
    seasons_dir = league_dir / "seasons"
    season_names = sorted([d.name for d in seasons_dir.iterdir() if d.is_dir()])
    
    if draft_pick_map is None:
        draft_pick_map = build_draft_pick_map(league_dir, season_names, players_db)

    # Pre-load all season data
    raw_seasons = {}
    for s in season_names:
        s_path = seasons_dir / s
        raw_seasons[s] = {
            "league": load_json(s_path / "league.json") or {},
            "users": load_json(s_path / "users.json") or [],
            "rosters": load_json(s_path / "rosters.json") or [],
            "drafts": load_json(s_path / "drafts.json") or [],
            "draft_picks": load_json(s_path / "draft_picks.json") or [],
            "transactions": load_json(s_path / "transactions.json") or [],
        }

    # Find user's roster_id per season
    roster_id_by_season = {}
    roster_owner_maps = {}
    for s, data in raw_seasons.items():
        user_map = {u.get("user_id"): u.get("display_name", "Unknown") for u in data["users"]}
        roster_map = {}
        for r in data["rosters"]:
            rid = r.get("roster_id")
            oid = r.get("owner_id")
            roster_map[rid] = user_map.get(oid, f"Team {rid}")
            if oid == uid:
                roster_id_by_season[s] = rid
        roster_owner_maps[s] = roster_map

    # Master player tracking: player_id -> metadata
    player_acquisitions = {}
    player_departures = {}
    player_seasons = defaultdict(list)
    all_user_trades = []
    all_user_draft_picks = []

    pos_rank = {p: i for i, p in enumerate(POSITION_ORDER)}

    # 1. First pass: Track all draft picks made by user
    for s in season_names:
        data = raw_seasons[s]
        my_rid = roster_id_by_season.get(s)
        for dp in data["draft_picks"]:
            if dp.get("roster_id") == my_rid:
                pid = dp.get("player_id")
                pinfo = players_db.get(pid, {})
                dtype = "Startup Draft" if s == "2019" else f"{s} Rookie Draft"
                acq_info = {
                    "method": "Draft",
                    "type_label": dtype,
                    "season": s,
                    "round": dp.get("round"),
                    "pick": dp.get("pick_no"),
                    "summary": f"{dtype} (Round {dp.get('round')}, Pick #{dp.get('pick_no')})"
                }
                if pid not in player_acquisitions:
                    player_acquisitions[pid] = acq_info
                
                all_user_draft_picks.append({
                    "season": s,
                    "round": dp.get("round"),
                    "pick_no": dp.get("pick_no"),
                    "player_id": pid,
                    "name": pinfo.get("full_name") or f"Player {pid}",
                    "position": pinfo.get("position", "OTHER"),
                    "team": pinfo.get("team", "FA"),
                    "draft_type": dtype
                })

    # 2. Second pass: Track all transactions (Trades, Waivers, FA) chronologically
    for s in season_names:
        data = raw_seasons[s]
        my_rid = roster_id_by_season.get(s)
        roster_map = roster_owner_maps[s]
        txs = sorted(data["transactions"], key=lambda x: x.get("created", 0))

        for tx in txs:
            created_ts = tx.get("created", 0)
            date_str = datetime.datetime.fromtimestamp(created_ts / 1000).strftime("%b %d, %Y") if created_ts else f"Season {s}"
            adds = tx.get("adds") or {}
            drops = tx.get("drops") or {}
            ttype = tx.get("type")

            # Check if this trade involves user
            if ttype == "trade" and my_rid in (tx.get("roster_ids") or []):
                other_rids = [r for r in tx.get("roster_ids", []) if r != my_rid]
                other_names = [roster_map.get(r, f"Team {r}") for r in other_rids]
                partner_str = ", ".join(other_names) if other_names else "League"

                dp_traded = tx.get("draft_picks") or []
                rec_players = [players_db.get(p, {}).get("full_name", p) for p, r in adds.items() if r == my_rid]
                sent_players = [players_db.get(p, {}).get("full_name", p) for p, r in drops.items() if r == my_rid]
                rec_picks = [format_traded_pick(p, draft_pick_map) for p in dp_traded if p.get("owner_id") == my_rid]
                sent_picks = [format_traded_pick(p, draft_pick_map) for p in dp_traded if p.get("previous_owner_id") == my_rid]

                trade_summary = {
                    "season": s,
                    "date": date_str,
                    "week": tx.get("week"),
                    "partner": partner_str,
                    "received_players": rec_players,
                    "received_picks": rec_picks,
                    "sent_players": sent_players,
                    "sent_picks": sent_picks
                }
                all_user_trades.append(trade_summary)

                # Players received in this trade
                for pid, r in adds.items():
                    if r == my_rid and pid not in player_acquisitions:
                        player_acquisitions[pid] = {
                            "method": "Trade",
                            "type_label": f"{s} Trade",
                            "season": s,
                            "partner": partner_str,
                            "summary": f"Trade with {partner_str} ({date_str})",
                            "trade_details": trade_summary
                        }

                # Players sent away in this trade
                for pid, r in drops.items():
                    if r == my_rid:
                        player_departures[pid] = {
                            "method": "Trade",
                            "season": s,
                            "partner": partner_str,
                            "date": date_str,
                            "summary": f"Traded to {partner_str} ({date_str})"
                        }

            # Waivers & FA additions
            if ttype in ("waiver", "free_agent"):
                for pid, r in adds.items():
                    if r == my_rid and pid not in player_acquisitions:
                        method_label = "Waivers" if ttype == "waiver" else "Free Agency"
                        faab = tx.get("settings", {}).get("waiver_bid", 0) if tx.get("settings") else 0
                        bid_str = f" (${faab} FAAB)" if faab and faab > 0 else ""
                        player_acquisitions[pid] = {
                            "method": method_label,
                            "type_label": f"{s} {method_label}",
                            "season": s,
                            "summary": f"Claimed via {method_label}{bid_str} on {date_str}"
                        }
                
                # Players dropped
                for pid, r in drops.items():
                    if r == my_rid and pid not in player_departures:
                        player_departures[pid] = {
                            "method": "Waivers",
                            "season": s,
                            "date": date_str,
                            "summary": f"Dropped on {date_str}"
                        }

    # 3. Third pass: Extract rosters per season and link acquisition data
    seasons_data = {}
    for s in season_names:
        data = raw_seasons[s]
        my_rid = roster_id_by_season.get(s)
        my_roster = next((r for r in data["rosters"] if r.get("roster_id") == my_rid), None)
        
        player_ids = my_roster.get("players", []) if my_roster else []
        starters = set(my_roster.get("starters", []) if my_roster else [])
        reserve = set(my_roster.get("reserve", []) or []) if my_roster else set()
        taxi = set(my_roster.get("taxi", []) or []) if my_roster else set()

        formatted_roster = []
        for pid in player_ids:
            pinfo = players_db.get(pid, {})
            pos = pinfo.get("position", "OTHER")
            status = "Starter" if pid in starters else ("IR" if pid in reserve else ("Taxi" if pid in taxi else "Bench"))
            
            player_seasons[pid].append(s)
            
            # Acquisition info
            acq = player_acquisitions.get(pid, {
                "method": "Pre-existing / Unknown",
                "type_label": f"Joined in {s}",
                "season": s,
                "summary": f"Acquired in {s}"
            })

            formatted_roster.append({
                "id": pid,
                "name": pinfo.get("full_name") or f"Player {pid}",
                "position": pos,
                "team": pinfo.get("team") or "FA",
                "status": status,
                "age": pinfo.get("age", "N/A"),
                "acquisition": acq,
                "tenure_start": acq.get("season", s)
            })

        formatted_roster.sort(key=lambda x: (pos_rank.get(x["position"], 99), x["name"]))

        # Count acquisition sources for this season's roster
        acq_breakdown = defaultdict(int)
        for p in formatted_roster:
            acq_breakdown[p["acquisition"]["method"]] += 1

        seasons_data[s] = {
            "season": s,
            "roster": formatted_roster,
            "player_ids": list(player_ids),
            "acq_breakdown": dict(acq_breakdown)
        }

    # 4. Fourth pass: Year-over-Year Transitions (How Team Changed)
    transitions = []
    for i in range(len(season_names)):
        curr_s = season_names[i]
        curr_data = seasons_data[curr_s]
        
        if i == 0:
            # Inaugural season (2019)
            transitions.append({
                "from_season": None,
                "to_season": curr_s,
                "label": f"Season {curr_s} (Inaugural Roster Genesis)",
                "joined": curr_data["roster"],
                "departed": [],
                "retained": []
            })
        else:
            prev_s = season_names[i - 1]
            prev_data = seasons_data[prev_s]
            
            curr_map = {p["id"]: p for p in curr_data["roster"]}
            prev_map = {p["id"]: p for p in prev_data["roster"]}
            
            joined_pids = set(curr_map.keys()) - set(prev_map.keys())
            departed_pids = set(prev_map.keys()) - set(curr_map.keys())
            retained_pids = set(curr_map.keys()) & set(prev_map.keys())
            
            joined_list = [curr_map[pid] for pid in joined_pids]
            joined_list.sort(key=lambda x: (pos_rank.get(x["position"], 99), x["name"]))
            
            departed_list = []
            for pid in departed_pids:
                p = prev_map[pid]
                dep_info = player_departures.get(pid, {
                    "method": "Offseason Roster Cut",
                    "season": curr_s,
                    "summary": f"Left roster before {curr_s}"
                })
                departed_list.append({
                    "id": pid,
                    "name": p["name"],
                    "position": p["position"],
                    "team": p["team"],
                    "departure": dep_info
                })
            departed_list.sort(key=lambda x: (pos_rank.get(x["position"], 99), x["name"]))
            
            retained_list = [curr_map[pid] for pid in retained_pids]
            retained_list.sort(key=lambda x: (pos_rank.get(x["position"], 99), x["name"]))
            
            transitions.append({
                "from_season": prev_s,
                "to_season": curr_s,
                "label": f"{prev_s} → {curr_s} Evolution",
                "joined": joined_list,
                "departed": departed_list,
                "retained": retained_list
            })

    # 5. Master all-time player directory
    all_players_master = []
    latest_season = season_names[-1]
    latest_pids = seasons_data[latest_season]["player_ids"]

    all_tracked_pids = set(list(player_acquisitions.keys()) + list(player_seasons.keys()))
    for pid in all_tracked_pids:
        pinfo = players_db.get(pid, {})
        acq = player_acquisitions.get(pid, {"method": "Unknown", "season": "Unknown", "summary": "Unknown"})
        dep = player_departures.get(pid, {"method": "Active", "summary": "Currently on Roster"} if pid in latest_pids else {"method": "Offseason Cut", "summary": "Roster Cut"})
        seasons_on_team = sorted(player_seasons.get(pid, []))
        
        all_players_master.append({
            "id": pid,
            "name": pinfo.get("full_name") or f"Player {pid}",
            "position": pinfo.get("position", "OTHER"),
            "team": pinfo.get("team") or "FA",
            "is_current": pid in latest_pids,
            "seasons": seasons_on_team,
            "seasons_count": len(seasons_on_team),
            "acquisition": acq,
            "departure": dep
        })

    all_players_master.sort(key=lambda x: (-x["seasons_count"], pos_rank.get(x["position"], 99), x["name"]))

    return {
        "user_id": uid,
        "seasons": season_names,
        "seasons_data": seasons_data,
        "transitions": transitions,
        "trades": all_user_trades,
        "draft_picks": all_user_draft_picks,
        "all_players": all_players_master
    }


def print_roster_console(season_data):
    s = season_data["season"]
    roster = season_data["roster"]
    breakdown = season_data["acq_breakdown"]
    
    print("\n" + "=" * 78)
    print(f"ROSTER FOR SEASON {s} (Total Players: {len(roster)})")
    print(f"Acquisition Mix: " + ", ".join(f"{k}: {v}" for k, v in breakdown.items()))
    print("=" * 78)
    
    current_pos = None
    for p in roster:
        pos = p["position"]
        if pos != current_pos:
            current_pos = pos
            print(f"\n[{pos}]")
            print("-" * 74)
            
        status = f"({p['status']})" if p["status"] != "Bench" else ""
        acq_tag = f"[{p['acquisition']['method']}: {p['acquisition']['summary']}]"
        print(f"  • {p['name']:<22} {p['team']:<4} {status:<10} {acq_tag}")
    print("\n" + "=" * 78)


def print_turnover_console(transition):
    label = transition["label"]
    joined = transition["joined"]
    departed = transition["departed"]
    retained = transition["retained"]
    
    print("\n" + "=" * 78)
    print(f"TEAM CHANGE ANALYSIS: {label}")
    print(f"Net Summary: +{len(joined)} Joined | -{len(departed)} Departed | {len(retained)} Retained")
    print("=" * 78)
    
    print(f"\n[+] PLAYERS JOINED ({len(joined)}):")
    if not joined:
        print("  None")
    for p in joined:
        print(f"  + {p['position']:<4} {p['name']:<22} ({p['team']:<3}) -> Acquired: {p['acquisition']['summary']}")
        
    print(f"\n[-] PLAYERS DEPARTED ({len(departed)}):")
    if not departed:
        print("  None")
    for p in departed:
        dep_str = p['departure']['summary']
        print(f"  - {p['position']:<4} {p['name']:<22} ({p['team']:<3}) -> Departure: {dep_str}")
        
    print(f"\n[★] CORE RETAINED ({len(retained)}):")
    for p in retained:
        print(f"  • {p['position']:<4} {p['name']:<22} (Original Acq: {p['acquisition']['type_label']})")
    print("=" * 78)


def get_all_league_managers(league_dir):
    """
    Finds all managers across all seasons in the league.
    """
    seasons_dir = league_dir / "seasons"
    season_names = sorted([d.name for d in seasons_dir.iterdir() if d.is_dir()])
    
    managers_dict = {}
    for s in season_names:
        users = load_json(seasons_dir / s / "users.json") or []
        for u in users:
            uid = u.get("user_id")
            if not uid:
                continue
            dname = u.get("display_name") or f"Manager_{uid}"
            tname = u.get("metadata", {}).get("team_name") or ""
            managers_dict[uid] = {
                "user_id": uid,
                "display_name": dname,
                "team_name": tname
            }
    return managers_dict


def export_data_files(league_dir, default_uid, players_db, user_history, draft_pick_map=None):
    """
    Exports organized dynasty history for all league managers to external files:
    - data/team_history.json (Pure JSON for data analysis/inspection)
    - team_data.js (External JavaScript bundle read by team_report.html)
    """
    print("\nCompiling full league history for all managers...")
    all_managers_meta = get_all_league_managers(league_dir)
    
    if draft_pick_map is None:
        seasons_dir = league_dir / "seasons"
        season_names = sorted([d.name for d in seasons_dir.iterdir() if d.is_dir()])
        draft_pick_map = build_draft_pick_map(league_dir, season_names, players_db)
    
    all_teams_export = {}
    for uid, info in all_managers_meta.items():
        if uid == default_uid:
            hist = user_history
        else:
            hist = build_full_history(league_dir, uid, players_db, draft_pick_map)
        
        # Keep only teams that had at least 1 rostered player
        seasons_with_rosters = [s for s, d in hist["seasons_data"].items() if len(d["roster"]) > 0]
        if not seasons_with_rosters:
            continue
            
        all_teams_export[uid] = {
            "user_id": uid,
            "display_name": info["display_name"],
            "team_name": info["team_name"],
            "seasons": hist["seasons"],
            "seasons_active": seasons_with_rosters,
            "seasons_data": {
                s: {
                    "season": s,
                    "roster": d["roster"],
                    "acq_breakdown": d["acq_breakdown"]
                }
                for s, d in hist["seasons_data"].items()
            },
            "transitions": hist["transitions"],
            "trades": hist["trades"],
            "draft_picks": hist["draft_picks"],
            "all_players": hist["all_players"]
        }

    # Sort managers so default_uid (jacques) is first, others alphabetical
    other_uids = sorted([uid for uid in all_teams_export if uid != default_uid], 
                        key=lambda uid: all_teams_export[uid]["display_name"].lower())
    ordered_uids = ([default_uid] if default_uid in all_teams_export else []) + other_uids
    
    managers_list = [
        {
            "user_id": uid,
            "display_name": all_teams_export[uid]["display_name"],
            "team_name": all_teams_export[uid]["team_name"],
            "seasons_count": len(all_teams_export[uid]["seasons_active"])
        }
        for uid in ordered_uids
    ]

    export_obj = {
        "default_manager_id": default_uid,
        "managers": managers_list,
        "teams": all_teams_export,
        # Keep root fields for backwards compatibility with single-manager mode
        "seasons": user_history["seasons"],
        "seasons_data": {
            s: {
                "season": s,
                "roster": data["roster"],
                "acq_breakdown": data["acq_breakdown"]
            }
            for s, data in user_history["seasons_data"].items()
        },
        "transitions": user_history["transitions"],
        "trades": user_history["trades"],
        "draft_picks": user_history["draft_picks"],
        "all_players": user_history["all_players"]
    }
    
    # 1. Pure JSON file in data/
    json_path = DATA_DIR / "team_history.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(export_obj, f, indent=2, ensure_ascii=False)
    print(f"Saved compiled league history JSON ({len(managers_list)} teams): {json_path.resolve()}")

    # 2. External JS bundle team_data.js (read by team_report.html)
    js_bundle_path = Path("team_data.js")
    with open(js_bundle_path, "w", encoding="utf-8") as f:
        f.write("window.appData = " + json.dumps(export_obj, ensure_ascii=False) + ";\n")
    print(f"Saved external JS data bundle: {js_bundle_path.resolve()}")

    # 3. Create standalone single-file HTML (sendable as 1 file on Discord/Email/iMessage)
    if Path("team_report.html").exists():
        html_template = Path("team_report.html").read_text(encoding="utf-8")
        js_content = Path("team_data.js").read_text(encoding="utf-8")
        standalone_html = html_template.replace('<script src="team_data.js"></script>', "<script>\n" + js_content + "\n</script>")
        standalone_path = Path("team_report_standalone.html")
        standalone_path.write_text(standalone_html, encoding="utf-8")
        
        # Also update index.html for hosting
        Path("index.html").write_text(html_template, encoding="utf-8")
        print(f"Saved single-file shareable HTML: {standalone_path.resolve()}")
    
    print(f"Interactive dashboard is ready: {Path('team_report.html').resolve()}")



def main():
    parser = argparse.ArgumentParser(description="Dynasty team evolution and acquisition organizer")
    parser.add_argument("--season", default=str(SEASON))
    parser.add_argument("--view", default=VIEW)
    args = parser.parse_args()

    target_season = str(args.season)
    view_mode = args.view.upper()

    user, league_dir = get_user_and_league()
    uid = user["user_id"]
    players_db = load_json(DATA_DIR / "players.json") or {}

    season_names = sorted([d.name for d in (league_dir / "seasons").iterdir() if d.is_dir()])
    draft_pick_map = build_draft_pick_map(league_dir, season_names, players_db)

    history = build_full_history(league_dir, uid, players_db, draft_pick_map)

    print("=" * 78)
    print("SLEEPER DYNASTY TEAM EVOLUTION ORGANIZER")
    print(f"Manager : {user.get('display_name')} (ID: {uid})")
    print(f"Season  : {target_season}")
    print(f"View    : {view_mode}")
    print("=" * 78)

    # Console output
    if view_mode in ("ROSTER", "ALL"):
        if target_season in history["seasons_data"]:
            print_roster_console(history["seasons_data"][target_season])
    
    if view_mode in ("MOVEMENT", "ALL"):
        # Print transitions
        matching_trans = [t for t in history["transitions"] if t["to_season"] == target_season]
        if matching_trans:
            print_turnover_console(matching_trans[0])
        elif target_season == "ALL":
            for t in history["transitions"]:
                print_turnover_console(t)

    # Export data files for interactive dashboard
    if GENERATE_HTML:
        export_data_files(league_dir, uid, players_db, history, draft_pick_map)


if __name__ == "__main__":
    main()
