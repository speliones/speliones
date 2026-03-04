import streamlit as st
import pandas as pd
import requests
from supabase import create_client, Client
from datetime import datetime, timezone
import dateutil.parser # Reikės įsidėti į requirements.txt

# --- KONFIGŪRACIJA ---
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except FileNotFoundError:
    st.error("Nustatymų failas nerastas. Sukonfigūruokite .streamlit/secrets.toml")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
SEASON = 2026

# --- PAGALBINĖS FUNKCIJOS ---

def get_race_schedule(round_num):
    """Gauna etapo tvarkaraštį, kad nustatytų deadline."""
    url = f"http://api.jolpi.ca/ergast/f1/{SEASON}/{round_num}.json"
    try:
        data = requests.get(url).json()
        race_data = data['MRData']['RaceTable']['Races'][0]
        
        # Bandome gauti Kvalifikacijos laiką (tai yra mūsų deadline)
        # Jei sprinto savaitgalis, reiktų tikrinti "Sprint" arba "Qualifying"
        if 'Qualifying' in race_data:
            deadline_str = f"{race_data['Qualifying']['date']}T{race_data['Qualifying']['time']}"
        else:
            # Fallback į lenktynių laiką
            deadline_str = f"{race_data['date']}T{race_data['time']}"
            
        # Konvertuojame į datetime objektą (UTC)
        deadline_dt = dateutil.parser.isoparse(deadline_str)
        # Užtikriname, kad laikas turi timezone info
        if deadline_dt.tzinfo is None:
            deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)
            
        return deadline_dt
    except:
        return None

def check_deadline(round_num):
    """Tikrina, ar dar galima spėti."""
    deadline = get_race_schedule(round_num)
    if not deadline:
        return True, "Nepavyko gauti tvarkaraščio (API error)."
    
    now = datetime.now(timezone.utc)
    
    if now > deadline:
        return False, f"Laikas baigėsi! Spėjimai priimami iki {deadline.strftime('%Y-%m-%d %H:%M')} UTC."
    
    return True, f"Spėjimus galima teikti iki {deadline.strftime('%Y-%m-%d %H:%M')} UTC."

def get_drivers():
    # ... (Ta pati funkcija kaip anksčiau) ...
    url = f"http://api.jolpi.ca/ergast/f1/{SEASON}/drivers.json?limit=100"
    try:
        response = requests.get(url)
        data = response.json()
        drivers = data['MRData']['DriverTable']['Drivers']
        return [f"{d['givenName']} {d['familyName']} ({d['code']})" for d in drivers]
    except:
        return ["Max Verstappen (VER)", "Lewis Hamilton (HAM)", "Charles Leclerc (LEC)"]

def get_race_results(round_num):
    # ... (Ta pati funkcija kaip anksčiau) ...
    # Nukopijuok iš ankstesnio atsakymo, čia niekas nesikeičia
    res_url = f"http://api.jolpi.ca/ergast/f1/{SEASON}/{round_num}/results.json"
    qual_url = f"http://api.jolpi.ca/ergast/f1/{SEASON}/{round_num}/qualifying.json"
    try:
        r_resp = requests.get(res_url).json()
        race_data = r_resp['MRData']['RaceTable']['Races'][0]['Results']
        top_10 = []
        fastest_lap_driver = None
        for driver in race_data:
            d_str = f"{driver['Driver']['givenName']} {driver['Driver']['familyName']} ({driver['Driver']['code']})"
            if int(driver['position']) <= 10:
                top_10.append(d_str)
            if 'FastestLap' in driver and driver['FastestLap']['rank'] == '1':
                fastest_lap_driver = d_str
        
        q_resp = requests.get(qual_url).json()
        q_data = q_resp['MRData']['RaceTable']['Races'][0]['QualifyingResults'][0]
        qualy_winner = f"{q_data['Driver']['givenName']} {q_data['Driver']['familyName']} ({q_data['Driver']['code']})"
        
        return {"top_10": top_10, "fastest_lap": fastest_lap_driver, "qualy_winner": qualy_winner}
    except:
        return None

def calculate_points(prediction, actual):
    # ... (Ta pati funkcija kaip anksčiau) ...
    # Nukopijuok visą logiką iš ankstesnio atsakymo
    score = 0
    details = []
    # (Čia įklijuok calculate_points logiką iš pirmo atsakymo)
    return score, details # Supaprastinau pavyzdyje, bet naudok pilną kodą

# --- AUTH LOGIKA ---
def login_user(username, password):
    """Patikrina arba sukuria vartotoją."""
    # 1. Bandome rasti vartotoją
    res = supabase.table("users").select("*").eq("username", username).execute()
    
    if len(res.data) > 0:
        # Vartotojas yra - tikriname slaptažodį
        user_data = res.data[0]
        if user_data['password'] == password:
            return True, "Sėkmingai prisijungta!"
        else:
            return False, "Neteisingas slaptažodis."
    else:
        # Vartotojo nėra - sukuriame naują (Registracija)
        try:
            supabase.table("users").insert({"username": username, "password": password}).execute()
            return True, "Vartotojas sukurtas ir prijungtas!"
        except Exception as e:
            return False, f"Klaida kuriant vartotoją: {e}"

# --- UI PRADŽIA ---
st.set_page_config(page_title="F-1 Spėlionė", page_icon="🏎️")
st.title("🏎️ F-1 Spėlionė")

# --- 1. PRISIJUNGIMAS (Login) ---
if 'user' not in st.session_state:
    st.session_state.user = None

with st.sidebar:
    if not st.session_state.user:
        st.header("Prisijungimas / Registracija")
        u_input = st.text_input("Vartotojas")
        p_input = st.text_input("Slaptažodis", type="password")
        if st.button("Prisijungti"):
            if u_input and p_input:
                success, msg = login_user(u_input, p_input)
                if success:
                    st.session_state.user = u_input
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
    else:
        st.write(f"Prisijungta: **{st.session_state.user}**")
        if st.button("Atsijungti"):
            st.session_state.user = None
            st.rerun()

if not st.session_state.user:
    st.info("Norėdami dalyvauti, prisijunkite kairėje.")
    st.stop()

# --- 2. PAGRINDINIS LANGAS ---
tab1, tab2 = st.tabs(["🎯 Mano Spėjimas", "🏆 Rezultatai"])

drivers_list = get_drivers()
round_num = st.number_input("Etapas (Round)", min_value=1, max_value=24, value=1)

# Tikriname deadline
is_open, time_msg = check_deadline(round_num)

with tab1:
    st.subheader(f"{round_num} Etapo Spėjimas")
    st.info(time_msg) # Rodo laiką

    # Jei jau užrakinta, tiesiog parodome esamą spėjimą (jei yra)
    # Čia supaprastinta: tiesiog neleidžiame Submit mygtuko
    
    with st.form("prediction_form"):
        col1, col2 = st.columns(2)
        with col1:
            q_winner = st.selectbox("Qualy Winner", drivers_list)
        with col2:
            f_lap = st.selectbox("Fastest Lap", drivers_list)
        
        st.write("TOP 10 Finišo tvarka:")
        picks = {}
        pick_values = []
        for i in range(1, 11):
            val = st.selectbox(f"Vieta {i}", drivers_list, key=f"p{i}")
            picks[f"p{i}"] = val
            pick_values.append(val)
        
        # Mygtukas matomas visada, bet logika viduje
        submitted = st.form_submit_button("Pateikti spėjimą", disabled=not is_open)
        
        if submitted:
            # 3. VALIDACIJA (Unikalumas)
            if len(set(pick_values)) != 10:
                st.error("❌ Klaida: Tas pats vairuotojas pasirinktas kelis kartus! TOP 10 turi būti skirtingi.")
            else:
                # Siunčiame į DB
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
                except Exception as e:
                    st.error(f"Klaida saugant: {e}")

with tab2:
    st.write("Rezultatų skaičiavimo logika (kaip anksčiau)...")
    # Čia įdėk rezultatų rodymo kodą iš praeito atsakymo
