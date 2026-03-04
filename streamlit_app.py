import streamlit as st
import pandas as pd
import requests
from supabase import create_client, Client
from datetime import datetime
from dateutil import parser
from zoneinfo import ZoneInfo

# --- KONFIGŪRACIJA ---
SEASON = 2025
TEST_MODE = True 
TIMEZONE = ZoneInfo("Europe/Vilnius")
ADMIN_USER = "Admin"  # Vartotojas, kuris turi teisę spausti mygtuką

try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except FileNotFoundError:
    st.error("❌ Nerastas secrets failas.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- FUNKCIJOS ---

def get_race_schedule(round_num):
    # ... (Ta pati funkcija kaip anksčiau - nekeičiame) ...
    url = f"http://api.jolpi.ca/ergast/f1/{SEASON}/{round_num}.json"
    try:
        data = requests.get(url, timeout=5).json()
        race_data = data['MRData']['RaceTable']['Races'][0]
        if 'Sprint' in race_data:
            deadline_str = f"{race_data['Sprint']['date']}T{race_data['Sprint']['time']}"
            event_type = "Sprinto"
        elif 'Qualifying' in race_data:
            deadline_str = f"{race_data['Qualifying']['date']}T{race_data['Qualifying']['time']}"
            event_type = "Kvalifikacijos"
        else:
            deadline_str = f"{race_data['date']}T{race_data['time']}"
            event_type = "Lenktynių"
        deadline_dt = parser.isoparse(deadline_str)
        if deadline_dt.tzinfo is None:
            deadline_dt = deadline_dt.replace(tzinfo=ZoneInfo("UTC"))
        return deadline_dt, event_type
    except:
        return None, "Error"

def check_deadline(round_num):
    # ... (Ta pati funkcija kaip anksčiau - nekeičiame) ...
    if TEST_MODE: return True, "🛠️ TEST MODE"
    deadline_utc, event_type = get_race_schedule(round_num)
    if not deadline_utc: return False, "⚠️ API Error"
    deadline_lt = deadline_utc.astimezone(TIMEZONE)
    now_lt = datetime.now(TIMEZONE)
    time_str = deadline_lt.strftime('%Y-%m-%d %H:%M')
    if now_lt > deadline_lt:
        return False, f"⛔ Spėjimai uždaryti! {event_type} pradžia buvo {time_str}."
    return True, f"✅ Iki {time_str}."

def get_drivers():
    # ... (Ta pati funkcija kaip anksčiau - nekeičiame) ...
    url = f"http://api.jolpi.ca/ergast/f1/{SEASON}/driverStandings.json"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        standings = data['MRData']['StandingsTable']['StandingsLists'][0]['DriverStandings']
        formatted = []
        for item in standings:
            d = item['Driver']
            c = item['Constructors'][0]['name']
            formatted.append({"display": f"[{c}] {d['givenName']} {d['familyName']} ({d['code']})", "team": c, "name": d['familyName']})
        formatted.sort(key=lambda x: (x['team'], x['name']))
        return [d['display'] for d in formatted]
    except:
        return ["Max Verstappen (VER)", "Lando Norris (NOR)", "Lewis Hamilton (HAM)"]

def extract_code(driver_str):
    try:
        return driver_str.split('(')[-1].replace(')', '').strip()
    except:
        return ""

# --- ATNAUJINTA LOGIKA: Skaičiavimas ---
def calculate_and_save_results(round_num):
    """Admin funkcija: pasiima rezultatus, suskaičiuoja VISIEMS ir įrašo į DB."""
    
    # 1. Gauname realius rezultatus
    base_url = "http://api.jolpi.ca/ergast/f1"
    try:
        r_resp = requests.get(f"{base_url}/{SEASON}/{round_num}/results.json", timeout=5).json()
        race_data = r_resp['MRData']['RaceTable']['Races'][0]['Results']
        q_resp = requests.get(f"{base_url}/{SEASON}/{round_num}/qualifying.json", timeout=5).json()
        q_data = q_resp['MRData']['RaceTable']['Races'][0]['QualifyingResults'][0]
        
        top_10_real = []
        fl_real = None
        for d in race_data:
            code = d['Driver']['code']
            if int(d['position']) <= 10: top_10_real.append(code)
            if 'FastestLap' in d and d['FastestLap']['rank'] == '1': fl_real = code
        q_real = q_data['Driver']['code']
    except Exception as e:
        return False, f"Nepavyko gauti API duomenų: {e}"

    # 2. Gauname visus vartotojų spėjimus šiam etapui
    predictions = supabase.table("predictions").select("*").eq("round_number", round_num).eq("season", SEASON).execute().data
    
    if not predictions:
        return False, "Nėra spėjimų šiam etapui."

    count = 0
    # 3. Ciklas per vartotojus
    for p in predictions:
        score = 0
        logs = [] # Saugosime trumpą info

        # Qualy
        user_q = extract_code(p['qualy_winner'])
        if user_q == q_real:
            score += 10
            logs.append("Q: ✅")
        else:
            logs.append(f"Q: ❌ (Real: {q_real})")

        # Fastest Lap
        user_fl = extract_code(p['fastest_lap'])
        if user_fl == fl_real:
            score += 10
            logs.append("FL: ✅")
        else:
            logs.append(f"FL: ❌ (Real: {fl_real})")

        # Top 10 Race
        race_pts = 0
        mistake_points = {0: 25, 1: 18, 2: 15, 3: 12, 4: 10, 5: 8, 6: 6, 7: 4, 8: 2, 9: 1}
        exact_bonus = {0: 10, 1: 6, 2: 4, 3: 3, 4: 2, 5: 1}

        for i in range(1, 11):
            u_pick = extract_code(p[f'p{i}'])
            if u_pick in top_10_real:
                real_idx = top_10_real.index(u_pick)
                diff = abs((i-1) - real_idx)
                pts = mistake_points.get(diff, 0)
                bonus = 0
                if diff == 0 and (i-1) in exact_bonus:
                    bonus = exact_bonus[i-1]
                
                race_pts += (pts + bonus)
        
        score += race_pts
        logs.append(f"Race: {race_pts} pts")
        
        # Sujungiame logus į stringą
        log_str = " | ".join(logs)

        # 4. Įrašome į DB (Update)
        supabase.table("predictions").update({
            "total_points": score,
            "breakdown": log_str
        }).eq("id", p['id']).execute()
        
        count += 1

    return True, f"Sėkmingai atnaujinta {count} vartotojų rezultatai!"

def login_user(username, password):
    res = supabase.table("users").select("*").eq("username", username).execute()
    if len(res.data) > 0:
        if res.data[0]['password'] == password: return True, "OK"
        else: return False, "Blogas slaptažodis"
    else:
        supabase.table("users").insert({"username": username, "password": password}).execute()
        return True, "Registracija sėkminga"

# --- UI START ---
st.set_page_config(page_title="F-1 Lyga", page_icon="🏎️", layout="wide")
st.title(f"🏎️ F-1 Lyga {SEASON}")

# LOGIN
if 'user' not in st.session_state: st.session_state.user = None
with st.sidebar:
    if not st.session_state.user:
        u = st.text_input("Vardas"); p = st.text_input("Slaptažodis", type="password")
        if st.button("Prisijungti"):
            ok, msg = login_user(u, p)
            if ok: st.session_state.user = u; st.rerun()
            else: st.error(msg)
    else:
        st.write(f"👤 **{st.session_state.user}**")
        if st.button("Atsijungti"): st.session_state.user = None; st.rerun()

if not st.session_state.user: st.stop()

# PAGRINDINIS LANGAS
tab1, tab2, tab3 = st.tabs(["📝 Spėjimas", "🏅 Etapo Rezultatai", "🏆 Sezono Įskaita"])

drivers_list = get_drivers()

# --- 1 TAB: SPĖJIMAS ---
with tab1:
    round_num = st.number_input("Etapas", 1, 24, 1)
    is_open, msg = check_deadline(round_num)
    if is_open: st.success(msg)
    else: st.error(msg)

    # Gauname esamą spėjimą
    existing = None
    res = supabase.table("predictions").select("*").eq("username", st.session_state.user).eq("round_number", round_num).eq("season", SEASON).execute()
    if res.data: existing = res.data[0]

    with st.form("form"):
        col1, col2 = st.columns(2)
        q_idx = drivers_list.index(existing['qualy_winner']) if existing and existing['qualy_winner'] in drivers_list else 0
        fl_idx = drivers_list.index(existing['fastest_lap']) if existing and existing['fastest_lap'] in drivers_list else 0
        
        q = col1.selectbox("Pole Position / Sprint Winner", drivers_list, index=q_idx)
        fl = col2.selectbox("Fastest Lap", drivers_list, index=fl_idx)
        
        st.write("---")
        st.write("**TOP 10 Spėjimas**")
        picks = {}
        picks_val = []
        cols = st.columns(5) + st.columns(5) # 10 stulpelių tinklelis
        for i in range(1, 11):
            def_idx = 0
            if existing and existing.get(f"p{i}") in drivers_list:
                def_idx = drivers_list.index(existing[f"p{i}"])
            val = cols[i-1].selectbox(f"P{i}", drivers_list, key=f"p{i}", index=def_idx)
            picks[f"p{i}"] = val
            picks_val.append(val)
            
        btn = st.form_submit_button("Išsaugoti", disabled=not is_open)
        if btn:
            if len(set(picks_val)) != 10: st.error("Dubliuojasi vairuotojai!")
            else:
                data = {"username": st.session_state.user, "round_number": round_num, "season": SEASON, "qualy_winner": q, "fastest_lap": fl, **picks}
                supabase.table("predictions").upsert(data, on_conflict="username, round_number, season").execute()
                st.success("Išsaugota!")

# --- 2 TAB: ETAPO REZULTATAI (Tik skaitymas + Admin mygtukas) ---
with tab2:
    st.header(f"Etapo #{round_num} Rezultatai")
    
    # ADMIN MYGTUKAS
    if st.session_state.user == ADMIN_USER:
        st.markdown("---")
        st.warning("👮 ADMIN ZONA")
        if st.button(f"🔄 SKAIČIUOTI IR IŠSAUGOTI REZULTATUS (Round {round_num})"):
            with st.spinner("Bendraujama su API ir atnaujinama DB..."):
                ok, res_msg = calculate_and_save_results(round_num)
                if ok: st.success(res_msg)
                else: st.error(res_msg)
        st.markdown("---")

    # Visiems vartotojams rodoma lentelė iš DB
    # Mes NEBEKVIEČIAME calculate_points čia. Mes tik skaitome.
    preds = supabase.table("predictions").select("*").eq("round_number", round_num).eq("season", SEASON).order("total_points", desc=True).execute().data
    
    if not preds:
        st.info("Šiam etapui spėjimų nėra arba rezultatai dar nepaskaičiuoti admino.")
    else:
        # Formuojame gražią lentelę
        table_data = []
        for p in preds:
            # Išvalome vairuotojų vardus, paliekame tik kodus (VER, HAM) kad tilptų į lentelę
            row = {
                "Vartotojas": p['username'],
                "Taškai": p['total_points'] if p['total_points'] else 0,
                "Qualy Spėjimas": extract_code(p['qualy_winner']),
                "Fastest Lap": extract_code(p['fastest_lap']),
                "Detalės": p['breakdown'] # Čia bus mūsų "Q: OK | Race: 50 pts" tekstas
            }
            table_data.append(row)
            
        df = pd.DataFrame(table_data)
        st.dataframe(
            df, 
            use_container_width=True,
            column_config={
                "Taškai": st.column_config.ProgressColumn(format="%d", min_value=0, max_value=150),
            }
        )

# --- 3 TAB: SEZONO ĮSKAITA (Sumavimas) ---
with tab3:
    st.header("🏆 Bendroji įskaita")
    
    # SQL užklausa gauti visus duomenis
    all_season_preds = supabase.table("predictions").select("username, total_points").eq("season", SEASON).execute().data
    
    if all_season_preds:
        df_season = pd.DataFrame(all_season_preds)
        # Sumuojame pagal vartotoją
        leaderboard = df_season.groupby("username")['total_points'].sum().reset_index()
        leaderboard = leaderboard.sort_values(by="total_points", ascending=False).reset_index(drop=True)
        
        # Pridedame "Vieta" stulpelį
        leaderboard.index += 1
        leaderboard.insert(0, 'Vieta', leaderboard.index)
        
        st.dataframe(leaderboard, use_container_width=True)
        
        # Grafikas
        st.bar_chart(leaderboard.set_index("username")['total_points'])
    else:
        st.info("Kol kas nėra duomenų sezonui.")
