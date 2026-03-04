import streamlit as st
import pandas as pd
import requests
from supabase import create_client, Client
from datetime import datetime
from dateutil import parser
from zoneinfo import ZoneInfo

# --- KONFIGŪRACIJA ---
SEASON = 2026
TEST_MODE = False        # Pakeisk į True testavimui (leidžia spėti po laiko)
TIMEZONE = ZoneInfo("Europe/Vilnius")
ADMIN_USER = "Admin"     # Vartotojas, turintis teisę skaičiuoti rezultatus

# Supabase prisijungimas
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except FileNotFoundError:
    st.error("❌ Nerastas .streamlit/secrets.toml failas su Supabase duomenimis.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- PAGALBINĖS FUNKCIJOS ---

def get_race_schedule(round_num):
    """Gauna etapo tvarkaraštį iš API."""
    url = f"http://api.jolpi.ca/ergast/f1/{SEASON}/{round_num}.json"
    try:
        data = requests.get(url, timeout=5).json()
        race_data = data['MRData']['RaceTable']['Races'][0]
        
        # Deadline prioritetas
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
    """Tikrina laiko limitą LT laiku."""
    if TEST_MODE:
        return True, "🛠️ TESTINIS REŽIMAS: Spėjimai atidaryti."

    deadline_utc, event_type = get_race_schedule(round_num)
    if not deadline_utc:
        return False, "⚠️ Nepavyko gauti tvarkaraščio iš API."
    
    deadline_lt = deadline_utc.astimezone(TIMEZONE)
    now_lt = datetime.now(TIMEZONE)
    time_str = deadline_lt.strftime('%Y-%m-%d %H:%M')
    
    if now_lt > deadline_lt:
        return False, f"⛔ Spėjimai uždaryti! {event_type} pradžia buvo {time_str}."
    
    return True, f"✅ Spėjimus galima teikti iki {time_str}."

def get_drivers():
    """Gauna vairuotojus. Jei API tuščias, grąžina pilną 2026 m. sąrašą."""
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
        
        if len(formatted) > 5: # Jei grąžino normalų sąrašą
            return [d['display'] for d in formatted]
        else:
            raise ValueError("Per mažai vairuotojų")
    except:
        # Pilnas 2025/2026 m. sąrašas testavimui / sezono pradžiai
        return [
            "[Alpine] Jack Doohan (DOO)", "[Alpine] Pierre Gasly (GAS)",
            "[Aston Martin] Fernando Alonso (ALO)", "[Aston Martin] Lance Stroll (STR)",
            "[Ferrari] Charles Leclerc (LEC)", "[Ferrari] Lewis Hamilton (HAM)",
            "[Haas] Esteban Ocon (OCO)", "[Haas] Oliver Bearman (BEA)",
            "[McLaren] Lando Norris (NOR)", "[McLaren] Oscar Piastri (PIA)",
            "[Mercedes] Andrea Kimi Antonelli (ANT)", "[Mercedes] George Russell (RUS)",
            "[RB] Isack Hadjar (HAD)", "[RB] Yuki Tsunoda (TSU)",
            "[Red Bull] Liam Lawson (LAW)", "[Red Bull] Max Verstappen (VER)",
            "[Sauber] Gabriel Bortoleto (BOR)", "[Sauber] Nico Hulkenberg (HUL)",
            "[Williams] Alexander Albon (ALB)", "[Williams] Carlos Sainz (SAI)"
        ]

def extract_code(driver_str):
    """Ištraukia vairuotojo kodą (pvz., VER) iš pilno pavadinimo."""
    try:
        return driver_str.split('(')[-1].replace(')', '').strip()
    except:
        return ""

def calculate_and_save_results(round_num):
    """ADMIN logika: Paskaičiuoja ir išsaugo taškus DB."""
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
            if int(d['position']) <= 10: 
                top_10_real.append(code)
            if 'FastestLap' in d and d['FastestLap']['rank'] == '1': 
                fl_real = code
                
        q_real = q_data['Driver']['code']
    except Exception as e:
        return False, f"Nepavyko gauti API duomenų (gal dar neįvyko?): {e}"

    predictions = supabase.table("predictions").select("*").eq("round_number", round_num).eq("season", SEASON).execute().data
    if not predictions:
        return False, "Nėra spėjimų šiam etapui."

    count = 0
    for p in predictions:
        score = 0
        logs = []

        # Qualy
        user_q = extract_code(p.get('qualy_winner', ''))
        if user_q == q_real:
            score += 10; logs.append("Q: ✅")
        else:
            logs.append(f"Q: ❌ (Real: {q_real})")

        # Fastest Lap
        user_fl = extract_code(p.get('fastest_lap', ''))
        if user_fl == fl_real:
            score += 10; logs.append("FL: ✅")
        else:
            logs.append(f"FL: ❌ (Real: {fl_real})")

        # Top 10 Race
        race_pts = 0
        mistake_points = {0: 25, 1: 18, 2: 15, 3: 12, 4: 10, 5: 8, 6: 6, 7: 4, 8: 2, 9: 1}
        exact_bonus = {0: 10, 1: 6, 2: 4, 3: 3, 4: 2, 5: 1}

        for i in range(1, 11):
            u_pick = extract_code(p.get(f'p{i}', ''))
            if u_pick in top_10_real:
                real_idx = top_10_real.index(u_pick)
                diff = abs((i-1) - real_idx)
                pts = mistake_points.get(diff, 0)
                bonus = exact_bonus.get(i-1, 0) if diff == 0 else 0
                race_pts += (pts + bonus)
        
        score += race_pts
        logs.append(f"Race: {race_pts} pts")
        
        log_str = " | ".join(logs)

        # Išsaugom atgal į DB
        supabase.table("predictions").update({
            "total_points": score,
            "breakdown": log_str
        }).eq("id", p['id']).execute()
        count += 1

    return True, f"Sėkmingai atnaujinta {count} vartotojų rezultatai!"

# --- AUTH SISTEMA ---

def login_user(username, password):
    """Paprasta prisijungimo / registracijos logika."""
    res = supabase.table("users").select("*").eq("username", username).execute()
    if len(res.data) > 0:
        if res.data[0]['password'] == password: 
            return True, "Prisijungta sėkmingai."
        else: 
            return False, "Neteisingas slaptažodis."
    else:
        try:
            supabase.table("users").insert({"username": username, "password": password}).execute()
            return True, "Naujas vartotojas sukurtas ir prijungtas!"
        except Exception as e:
            return False, f"Klaida kuriant vartotoją: {e}"

# --- USER INTERFACE (UI) ---

st.set_page_config(page_title="F-1 Lyga", page_icon="🏎️", layout="wide")
st.title(f"🏎️ F-1 Lyga ({SEASON})")

if 'user' not in st.session_state:
    st.session_state.user = None

with st.sidebar:
    st.markdown("### 👤 Paskyra")
    if not st.session_state.user:
        u_input = st.text_input("Vardas")
        p_input = st.text_input("Slaptažodis", type="password")
        if st.button("Prisijungti / Registruotis"):
            if u_input and p_input:
                ok, msg = login_user(u_input, p_input)
                if ok: 
                    st.session_state.user = u_input
                    st.rerun()
                else: 
                    st.error(msg)
            else:
                st.warning("Įveskite abu laukus.")
    else:
        st.success(f"Prisijungta: **{st.session_state.user}**")
        if st.button("Atsijungti"): 
            st.session_state.user = None
            st.rerun()

if not st.session_state.user:
    st.info("👈 Prisijunkite meniu juostoje kairėje.")
    st.stop()

# --- PAGRINDINIAI TABAI ---
tab1, tab2, tab3 = st.tabs(["📝 Etapo Spėjimas", "🏅 Etapo Rezultatai", "🏆 Sezono Įskaita"])

round_num = st.sidebar.number_input("Pasirinkti etapą (Round)", min_value=1, max_value=24, value=1)
drivers_list = get_drivers()

# --- 1 TAB: SPĖJIMAS ---
with tab1:
    st.header(f"Etapas #{round_num}")
    is_open, time_msg = check_deadline(round_num)
    
    if is_open: st.success(time_msg)
    else: st.error(time_msg)

    existing_pred = None
    resp = supabase.table("predictions").select("*").eq("username", st.session_state.user).eq("round_number", round_num).eq("season", SEASON).execute()
    if resp.data: existing_pred = resp.data[0]

    with st.form("prediction_form"):
        col1, col2 = st.columns(2)
        q_idx = drivers_list.index(existing_pred['qualy_winner']) if existing_pred and existing_pred.get('qualy_winner') in drivers_list else 0
        fl_idx = drivers_list.index(existing_pred['fastest_lap']) if existing_pred and existing_pred.get('fastest_lap') in drivers_list else 0
        
        q_winner = col1.selectbox("Qualy / Sprint Winner", drivers_list, index=q_idx)
        f_lap = col2.selectbox("Fastest Lap", drivers_list, index=fl_idx)
        
        st.write("---")
        st.write("**TOP 10 Finišo tvarka:**")
        picks = {}
        pick_values = []
        
        # Sukuriame dviejų eilių po 5 stulpelius išdėstymą
        row1 = st.columns(5)
        row2 = st.columns(5)
        cols = row1 + row2 
        
        for i in range(1, 11):
            def_idx = 0
            if existing_pred and existing_pred.get(f"p{i}") in drivers_list:
                def_idx = drivers_list.index(existing_pred[f"p{i}"])
            
            val = cols[i-1].selectbox(f"P{i}", drivers_list, key=f"p{i}", index=def_idx)
            picks[f"p{i}"] = val
            pick_values.append(val)
        
        submitted = st.form_submit_button("💾 Išsaugoti", disabled=not is_open)
        if submitted:
            if len(set(pick_values)) != 10:
                st.error("❌ KLAIDA: TOP 10 privalo būti skirtingi vairuotojai!")
            else:
                data = {"username": st.session_state.user, "round_number": round_num, "season": SEASON, "qualy_winner": q_winner, "fastest_lap": f_lap, **picks}
                supabase.table("predictions").upsert(data, on_conflict="username, round_number, season").execute()
                st.success("✅ Spėjimas sėkmingai išsaugotas!")
                st.balloons()

# --- 2 TAB: ETAPO REZULTATAI ---
with tab2:
    st.header(f"Rezultatai: Etapas #{round_num}")
    
    # Tik Admin gali paleisti skaičiavimą
    if st.session_state.user == ADMIN_USER:
        st.markdown("---")
        st.warning("👮 ADMIN ZONA")
        if st.button(f"🔄 SKAIČIUOTI REZULTATUS (Round {round_num})"):
            with st.spinner("Bendraujama su API ir atnaujinama duomenų bazė..."):
                ok, res_msg = calculate_and_save_results(round_num)
                if ok: st.success(res_msg)
                else: st.error(res_msg)
        st.markdown("---")

    # Paprasti vartotojai tiesiog mato išsaugotus rezultatus iš DB
    preds = supabase.table("predictions").select("*").eq("round_number", round_num).eq("season", SEASON).order("total_points", desc=True).execute().data
    
    if not preds:
        st.info("Šiam etapui spėjimų dar nėra.")
    else:
        table_data = []
        for p in preds:
            table_data.append({
                "Vartotojas": p['username'],
                "Taškai": p.get('total_points') or 0,
                "Qualy": extract_code(p.get('qualy_winner', '')),
                "Fast L": extract_code(p.get('fastest_lap', '')),
                "Detalės": p.get('breakdown', 'Dar nesuskaičiuota')
            })
            
        if table_data:
            df = pd.DataFrame(table_data)
            st.dataframe(df, use_container_width=True, hide_index=True)

# --- 3 TAB: SEZONO ĮSKAITA ---
with tab3:
    st.header("🏆 Bendra Sezono Įskaita")
    
    all_preds = supabase.table("predictions").select("username, total_points").eq("season", SEASON).execute().data
    
    if all_preds:
        df_season = pd.DataFrame(all_preds)
        # Sumuojame pagal vartotoją
        leaderboard = df_season.groupby("username")['total_points'].sum().reset_index()
        leaderboard = leaderboard.sort_values(by="total_points", ascending=False).reset_index(drop=True)
        
        # Pridedame "Vieta" stulpelį
        leaderboard.index += 1
        leaderboard.insert(0, 'Vieta', leaderboard.index)
        
        st.dataframe(leaderboard, use_container_width=True, hide_index=True)
        st.bar_chart(leaderboard.set_index("username")['total_points'])
    else:
        st.info("Kol kas nėra jokių taškų šiame sezone.")
