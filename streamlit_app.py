import streamlit as st
import pandas as pd
import requests
from supabase import create_client, Client
from datetime import datetime
from dateutil import parser
from zoneinfo import ZoneInfo # Reikalingas LT laikui

# --- KONFIGŪRACIJA ---

# Nustatymai
SEASON = 2026           # Einamasis sezonas
TEST_MODE = False       # Pakeisk į True, jei nori testuoti (leidžia spėti bet kada)
TIMEZONE = ZoneInfo("Europe/Vilnius")

# Supabase prisijungimas
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except FileNotFoundError:
    st.error("❌ Nerastas .streamlit/secrets.toml failas su Supabase duomenimis.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- PAGALBINĖS FUNKCIJOS: API IR DUOMENYS ---

def get_race_schedule(round_num):
    """Gauna etapo tvarkaraštį ir nustato deadline laiką."""
    url = f"http://api.jolpi.ca/ergast/f1/{SEASON}/{round_num}.json"
    try:
        data = requests.get(url, timeout=5).json()
        race_data = data['MRData']['RaceTable']['Races'][0]
        
        # Deadline prioritetas: 1. Sprintas, 2. Kvalifikacija, 3. Lenktynės
        if 'Sprint' in race_data:
            deadline_str = f"{race_data['Sprint']['date']}T{race_data['Sprint']['time']}"
            event_type = "Sprinto"
        elif 'Qualifying' in race_data:
            deadline_str = f"{race_data['Qualifying']['date']}T{race_data['Qualifying']['time']}"
            event_type = "Kvalifikacijos"
        else:
            deadline_str = f"{race_data['date']}T{race_data['time']}"
            event_type = "Lenktynių"
            
        # Konvertuojame į UTC datetime objektą
        deadline_dt = parser.isoparse(deadline_str)
        if deadline_dt.tzinfo is None:
            deadline_dt = deadline_dt.replace(tzinfo=ZoneInfo("UTC"))
            
        return deadline_dt, event_type
    except Exception as e:
        return None, str(e)

def check_deadline(round_num):
    """Tikrina, ar dar galima spėti, atsižvelgiant į LT laiką."""
    if TEST_MODE:
        return True, "🛠️ TESTINIS REŽIMAS: Spėjimai priimami visada."

    deadline_utc, event_type = get_race_schedule(round_num)
    
    if not deadline_utc:
        return False, "⚠️ Nepavyko gauti tvarkaraščio iš API."
    
    # Konvertuojame deadline ir dabartinį laiką į LT zoną
    deadline_lt = deadline_utc.astimezone(TIMEZONE)
    now_lt = datetime.now(TIMEZONE)
    
    time_str = deadline_lt.strftime('%Y-%m-%d %H:%M')
    
    if now_lt > deadline_lt:
        return False, f"⛔ Spėjimai uždaryti! {event_type} pradžia buvo {time_str} (LT laiku)."
    
    return True, f"✅ Spėjimus galima teikti iki {time_str} (LT laiku)."

def get_drivers():
    """Bando gauti vairuotojus su komandomis. Jei nepavyksta, ima paprastą sąrašą."""
    # 1. Bandome gauti su komandomis (iš Standings)
    url = f"http://api.jolpi.ca/ergast/f1/{SEASON}/driverStandings.json"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        standings = data['MRData']['StandingsTable']['StandingsLists'][0]['DriverStandings']
        
        formatted_drivers = []
        for item in standings:
            driver = item['Driver']
            constructor = item['Constructors'][0]['name']
            d_str = f"[{constructor}] {driver['givenName']} {driver['familyName']} ({driver['code']})"
            
            formatted_drivers.append({"display": d_str, "team": constructor, "name": driver['familyName']})
            
        # Rūšiuojame pagal komandą, tada pagal pavardę
        formatted_drivers.sort(key=lambda x: (x['team'], x['name']))
        return [d['display'] for d in formatted_drivers]

    except:
        # 2. Fallback: Jei sezonas naujas ir nėra taškų, imam paprastą sąrašą be komandų
        try:
            url_fallback = f"http://api.jolpi.ca/ergast/f1/{SEASON}/drivers.json?limit=100"
            resp = requests.get(url_fallback, timeout=5).json()
            drivers = resp['MRData']['DriverTable']['Drivers']
            return [f"{d['givenName']} {d['familyName']} ({d['code']})" for d in drivers]
        except:
            # 3. Extra Fallback (pvz. jei API visai miręs)
            return ["Max Verstappen (VER)", "Lewis Hamilton (HAM)", "Lando Norris (NOR)", "Charles Leclerc (LEC)"]

def get_race_results(round_num):
    """Gauna realius rezultatus taškų skaičiavimui."""
    base_url = "http://api.jolpi.ca/ergast/f1"
    try:
        # Race Results
        r_resp = requests.get(f"{base_url}/{SEASON}/{round_num}/results.json", timeout=5).json()
        race_data = r_resp['MRData']['RaceTable']['Races'][0]['Results']
        
        # Qualy Results
        q_resp = requests.get(f"{base_url}/{SEASON}/{round_num}/qualifying.json", timeout=5).json()
        q_data = q_resp['MRData']['RaceTable']['Races'][0]['QualifyingResults'][0]

        top_10 = []
        fastest_lap_driver = None
        
        # Helper function to format driver string consistently
        def format_driver(d_obj):
            # Mes čia tikriname pagal Code (pvz VER), nes tai patikimiausia
            return d_obj['code']

        # Surenkame TOP 10 kodus
        for driver in race_data:
            code = driver['Driver']['code']
            if int(driver['position']) <= 10:
                top_10.append(code)
            
            if 'FastestLap' in driver and driver['FastestLap']['rank'] == '1':
                fastest_lap_driver = code

        qualy_winner = q_data['Driver']['code']

        return {
            "top_10": top_10,
            "fastest_lap": fastest_lap_driver,
            "qualy_winner": qualy_winner
        }
    except (IndexError, KeyError, requests.exceptions.RequestException):
        return None

def extract_code(driver_str):
    """Ištraukia kodą (pvz VER) iš stringo '[Red Bull] Max Verstappen (VER)'"""
    try:
        return driver_str.split('(')[-1].replace(')', '').strip()
    except:
        return ""

def calculate_points(prediction, actual):
    """Skaičiuoja taškus pagal F-1.lt logiką."""
    score = 0
    details = []

    # 1. Kvalifikacija (10 tšk.)
    p_qualy = extract_code(prediction['qualy_winner'])
    if p_qualy == actual['qualy_winner']:
        score += 10
        details.append("Qualy: +10")

    # 2. Greičiausias ratas (10 tšk.)
    p_fast = extract_code(prediction['fastest_lap'])
    if p_fast == actual['fastest_lap']:
        score += 10
        details.append("Fastest Lap: +10")

    # 3. TOP 10
    mistake_points = {0: 25, 1: 18, 2: 15, 3: 12, 4: 10, 5: 8, 6: 6, 7: 4, 8: 2, 9: 1}
    exact_bonus = {0: 10, 1: 6, 2: 4, 3: 3, 4: 2, 5: 1} # 1-6 vieta (indeksas 0-5)

    actual_list = actual['top_10'] # List of codes

    for i in range(1, 11):
        p_driver_str = prediction[f'p{i}']
        p_code = extract_code(p_driver_str)
        
        pred_idx = i - 1 # 0-based index

        if p_code in actual_list:
            real_idx = actual_list.index(p_code)
            diff = abs(pred_idx - real_idx)
            
            pts = mistake_points.get(diff, 0)
            
            # Bonus už tikslią vietą (tik 1-6 vietoms)
            bonus = 0
            if diff == 0 and pred_idx in exact_bonus:
                bonus = exact_bonus[pred_idx]
            
            total = pts + bonus
            score += total
            if total > 0:
                details.append(f"P{i} ({p_code}): +{total} (Diff: {diff})")
        else:
            # Nepateko į top 10
            pass

    return score, details

# --- AUTH SISTEMA ---

def login_user(username, password):
    # 1. Ieškome vartotojo
    res = supabase.table("users").select("*").eq("username", username).execute()
    
    if len(res.data) > 0:
        user_data = res.data[0]
        if user_data['password'] == password:
            return True, "Sėkmingai prisijungta!"
        else:
            return False, "Neteisingas slaptažodis."
    else:
        # 2. Registracija
        try:
            supabase.table("users").insert({"username": username, "password": password}).execute()
            return True, "Vartotojas sukurtas ir prijungtas!"
        except Exception as e:
            return False, f"Klaida: {e}"

# --- USER INTERFACE (UI) ---

st.set_page_config(page_title="F-1 Spėlionė", page_icon="🏎️", layout="wide")

st.title(f"🏎️ F-1 Spėlionė ({SEASON})")

if TEST_MODE:
    st.warning("🛠️ ĮJUNGTAS TESTAVIMO REŽIMAS: Deadline išjungtas, galima spėti bet kada.")

# 1. PRISIJUNGIMAS
if 'user' not in st.session_state:
    st.session_state.user = None

with st.sidebar:
    st.markdown("### 👤 Vartotojas")
    if not st.session_state.user:
        u_input = st.text_input("Vardas")
        p_input = st.text_input("Slaptažodis", type="password")
        if st.button("Prisijungti / Registruotis"):
            if u_input and p_input:
                success, msg = login_user(u_input, p_input)
                if success:
                    st.session_state.user = u_input
                    st.success(msg)
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
    st.info("👈 Prisijunkite arba užsiregistruokite kairėje meniu juostoje.")
    st.stop()

# 2. PAGRINDINIS TURINYS
tab1, tab2 = st.tabs(["📝 Mano Spėjimas", "🏆 Rezultatai"])

round_num = st.sidebar.number_input("Pasirinkti etapą (Round)", min_value=1, max_value=24, value=1)
drivers_list = get_drivers()

# --- TAB 1: SPĖJIMAS ---
with tab1:
    st.header(f"Etapas #{round_num}")
    
    # Tikriname laiką
    is_open, time_msg = check_deadline(round_num)
    
    if is_open:
        st.success(time_msg)
    else:
        st.error(time_msg)

    # Tikriname, ar jau yra spėjimas
    existing_pred = None
    try:
        resp = supabase.table("predictions").select("*")\
            .eq("username", st.session_state.user)\
            .eq("round_number", round_num)\
            .eq("season", SEASON).execute()
        if resp.data:
            existing_pred = resp.data[0]
            st.info("ℹ️ Jūs jau turite išsaugotą spėjimą šiam etapui. Galite jį atnaujinti, jei laikas dar nesibaigė.")
    except:
        pass

    # Forma
    with st.form("prediction_form"):
        col1, col2 = st.columns(2)
        with col1:
            def_q = drivers_list.index(existing_pred['qualy_winner']) if existing_pred and existing_pred['qualy_winner'] in drivers_list else 0
            q_winner = st.selectbox("Qualy Winner (Sprint jei yra)", drivers_list, index=def_q)
        
        with col2:
            def_f = drivers_list.index(existing_pred['fastest_lap']) if existing_pred and existing_pred['fastest_lap'] in drivers_list else 0
            f_lap = st.selectbox("Fastest Lap", drivers_list, index=def_f)
        
        st.subheader("TOP 10 Finišo tvarka")
        
        picks = {}
        pick_values = []
        
        # Generuojame 10 pasirinkimų
        for i in range(1, 11):
            def_val_idx = 0
            if existing_pred:
                # Bandome atstatyti buvusį pasirinkimą
                prev_val = existing_pred.get(f"p{i}")
                # Reikia rasti to stringo indeksą drivers_list sąraše
                # Kadangi formatavimas gali skirtis, ieškome dalinio atitikmens arba kodo
                # Čia supaprastintai:
                if prev_val in drivers_list:
                    def_val_idx = drivers_list.index(prev_val)
            
            val = st.selectbox(f"{i} Vieta", drivers_list, key=f"p{i}", index=def_val_idx)
            picks[f"p{i}"] = val
            pick_values.append(val)
        
        # Mygtukas
        submitted = st.form_submit_button("💾 Išsaugoti Spėjimą", disabled=not is_open)
        
        if submitted:
            # Validacija: Unikalumas
            if len(set(pick_values)) != 10:
                st.error("❌ KLAIDA: Pasirinkote tą patį vairuotoją kelis kartus! TOP 10 privalo būti skirtingi.")
            else:
                data = {
                    "username": st.session_state.user,
                    "round_number": round_num,
                    "season": SEASON,
                    "qualy_winner": q_winner,
                    "fastest_lap": f_lap,
                    **picks
                }
                try:
                    supabase.table("predictions").upsert(data, on_conflict="username, round_number, season").execute()
                    st.success("✅ Spėjimas sėkmingai išsaugotas!")
                    st.balloons()
                except Exception as e:
                    st.error(f"Klaida saugant duomenis: {e}")

# --- TAB 2: REZULTATAI ---
with tab2:
    st.header("Lyderių lentelė")
    
    if st.button("🔄 Skaičiuoti / Atnaujinti rezultatus"):
        with st.spinner("Gaunami realūs rezultatai iš API..."):
            actual_results = get_race_results(round_num)
        
        if not actual_results:
            st.warning("⚠️ Rezultatų dar nėra arba nepavyko susisiekti su API.")
        else:
            # Gauname visus spėjimus
            all_preds = supabase.table("predictions").select("*")\
                .eq("round_number", round_num)\
                .eq("season", SEASON).execute().data
            
            if not all_preds:
                st.info("Šiam etapui spėjimų nėra.")
            else:
                leaderboard = []
                for p in all_preds:
                    pts, logs = calculate_points(p, actual_results)
                    leaderboard.append({
                        "Vartotojas": p['username'],
                        "Taškai": pts,
                        "Informacija": logs
                    })
                
                # Rūšiuojame ir rodome
                df = pd.DataFrame(leaderboard).sort_values(by="Taškai", ascending=False)
                
                # Gražesnis atvaizdavimas
                st.dataframe(
                    df, 
                    column_config={
                        "Informacija": st.column_config.ListColumn("Kaip gauti taškai")
                    },
                    use_container_width=True
                )
                
                with st.expander("👀 Rodyti realius API rezultatus (Debug)"):
                    st.json(actual_results)
