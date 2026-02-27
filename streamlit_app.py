import streamlit as st
import pandas as pd
import requests
from supabase import create_client, Client

# --- KONFIGŪRACIJA ---
# Šiuos duomenis vėliau įkelsime į Streamlit Secrets
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

# Inicijuojame Supabase klientą
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Sezono nustatymai
SEASON = 2026

# --- PAGALBINĖS FUNKCIJOS ---

def get_drivers():
    """Gauna 2026 sezono vairuotojų sąrašą iš API."""
    url = f"http://api.jolpi.ca/ergast/f1/{SEASON}/drivers.json?limit=100"
    try:
        response = requests.get(url)
        data = response.json()
        drivers = data['MRData']['DriverTable']['Drivers']
        # Grąžiname formatu "V. Pavardė (KODAS)"
        return [f"{d['givenName']} {d['familyName']} ({d['code']})" for d in drivers]
    except:
        # Fallback jei API neveikia (testavimui)
        return ["Max Verstappen (VER)", "Lewis Hamilton (HAM)", "Charles Leclerc (LEC)", "Lando Norris (NOR)", "George Russell (RUS)"]

def get_race_results(round_num):
    """Gauna realius lenktynių rezultatus."""
    # Rezultatai
    res_url = f"http://api.jolpi.ca/ergast/f1/{SEASON}/{round_num}/results.json"
    # Kvalifikacija (Pole position)
    qual_url = f"http://api.jolpi.ca/ergast/f1/{SEASON}/{round_num}/qualifying.json"
    
    try:
        # 1. Race Results & Fastest Lap
        r_resp = requests.get(res_url).json()
        race_data = r_resp['MRData']['RaceTable']['Races'][0]['Results']
        
        # Ištraukiam TOP 10 finišą
        top_10 = []
        fastest_lap_driver = None
        
        for driver in race_data:
            d_str = f"{driver['Driver']['givenName']} {driver['Driver']['familyName']} ({driver['Driver']['code']})"
            if int(driver['position']) <= 10:
                top_10.append(d_str)
            
            # Ieškome greičiausio rato (Fastest Lap rank = 1)
            if 'FastestLap' in driver and driver['FastestLap']['rank'] == '1':
                fastest_lap_driver = d_str

        # 2. Qualy Winner
        q_resp = requests.get(qual_url).json()
        q_data = q_resp['MRData']['RaceTable']['Races'][0]['QualifyingResults'][0]
        qualy_winner = f"{q_data['Driver']['givenName']} {q_data['Driver']['familyName']} ({q_data['Driver']['code']})"

        return {
            "top_10": top_10,
            "fastest_lap": fastest_lap_driver,
            "qualy_winner": qualy_winner
        }
    except IndexError:
        return None # Rezultatų dar nėra

def calculate_points(prediction, actual):
    """Skaičiuoja taškus pagal seną F-1.lt logiką."""
    score = 0
    details = []

    # 1. Kvalifikacija (10 tšk.)
    if prediction['qualy_winner'] == actual['qualy_winner']:
        score += 10
        details.append("Kvalifikacija: +10")

    # 2. Greičiausias ratas (10 tšk.)
    if prediction['fastest_lap'] == actual['fastest_lap']:
        score += 10
        details.append("Greičiausias ratas: +10")

    # 3. TOP 10 logika
    # Bonusai už tikslią vietą
    exact_bonus = {0: 10, 1: 6, 2: 4, 3: 3, 4: 2, 5: 1} # Indeksai 0-5 atitinka 1-6 vietas
    # Baudos už paklaidą (mapping pagal tavo aprašymą)
    # 0 diff = 25 (bazė) + bonus
    # 1 diff = 18
    # 2 diff = 15
    # 3 diff = 12
    # Toliau mažinam po 1 ar 2, bet čia supaprastinsim iki 9 diff = 1
    
    mistake_points = {
        0: 25, 1: 18, 2: 15, 3: 12, 4: 10, 5: 8, 6: 6, 7: 4, 8: 2, 9: 1
    }

    pred_list = [prediction[f'p{i}'] for i in range(1, 11)]
    actual_list = actual['top_10'] # Čia yra tvarkingas sąrašas 1-10 vietos

    for i, p_driver in enumerate(pred_list):
        # i yra spėta vieta (0 indeksas = 1 vieta)
        if p_driver in actual_list:
            real_idx = actual_list.index(p_driver)
            diff = abs(i - real_idx)
            
            pts = mistake_points.get(diff, 0)
            
            # Pridedame bonusą už tikslią vietą (jei diff 0 ir vieta 1-6)
            bonus = 0
            if diff == 0 and i in exact_bonus:
                bonus = exact_bonus[i]
            
            total_driver_pts = pts + bonus
            score += total_driver_pts
            if total_driver_pts > 0:
                details.append(f"P{i+1} ({p_driver}): +{total_driver_pts} (Diff: {diff})")
        else:
            # Vairuotojas nepateko į TOP 10
            pass

    return score, details

# --- INTERFEISAS (UI) ---

st.set_page_config(page_title="F-1 Spėlionė", page_icon="🏎️")
st.title("🏎️ F-1 Senoji Spėlionė")

# 1. Prisijungimas (Paprastas)
if 'user' not in st.session_state:
    st.session_state.user = None

with st.sidebar:
    st.header("Vartotojas")
    username_input = st.text_input("Įveskite slapyvardį")
    if st.button("Prisijungti"):
        if username_input:
            st.session_state.user = username_input
            st.success(f"Sveiki, {username_input}!")
            st.rerun()

if not st.session_state.user:
    st.warning("Prašome įvesti slapyvardį kairėje, kad galėtumėte spėlioti.")
    st.stop()

# 2. Pagrindinis Langas
tab1, tab2 = st.tabs(["🎯 Mano Spėjimas", "🏆 Rezultatai"])

drivers_list = get_drivers()
round_num = st.number_input("Pasirinkite etapą (Round)", min_value=1, max_value=24, value=1)

with tab1:
    st.subheader(f"{round_num} Etapo Spėjimas")
    
    with st.form("prediction_form"):
        col1, col2 = st.columns(2)
        with col1:
            q_winner = st.selectbox("Qualy Winner", drivers_list)
        with col2:
            f_lap = st.selectbox("Fastest Lap", drivers_list)
        
        st.divider()
        st.write("TOP 10 Finišo tvarka:")
        
        picks = {}
        for i in range(1, 11):
            picks[f"p{i}"] = st.selectbox(f"Vieta {i}", drivers_list, key=f"p{i}")
        
        submitted = st.form_submit_button("Pateikti spėjimą")
        
        if submitted:
            data = {
                "username": st.session_state.user,
                "round_number": round_num,
                "season": SEASON,
                "qualy_winner": q_winner,
                "fastest_lap": f_lap,
                **picks
            }
            
            # Siunčiame į Supabase (upsert)
            try:
                supabase.table("predictions").upsert(data, on_conflict="username, round_number, season").execute()
                st.success("Spėjimas išsaugotas!")
            except Exception as e:
                st.error(f"Klaida saugant: {e}")

with tab2:
    st.subheader("Rezultatai ir Taškai")
    
    if st.button("Skaičiuoti rezultatus"):
        actual_results = get_race_results(round_num)
        
        if not actual_results:
            st.error("Šio etapo realių rezultatų dar nėra API sistemoje.")
        else:
            # Gauname visus spėjimus šiam etapui
            resp = supabase.table("predictions").select("*").eq("round_number", round_num).eq("season", SEASON).execute()
            predictions = resp.data
            
            leaderboard = []
            
            for p in predictions:
                pts, log = calculate_points(p, actual_results)
                leaderboard.append({"Vartotojas": p['username'], "Taškai": pts, "Detalės": str(log)})
            
            df = pd.DataFrame(leaderboard).sort_values(by="Taškai", ascending=False)
            st.dataframe(df, use_container_width=True)
            
            # Parodyti realius rezultatus palyginimui
            st.write("---")
            st.write("**Realūs rezultatai:**")
            st.json(actual_results)
