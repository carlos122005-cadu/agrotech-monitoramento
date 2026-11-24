import os
import math
import requests
import pandas as pd
from io import StringIO
from datetime import datetime, timezone, timedelta

NASA_FIRMS_KEY = (os.getenv("NASA_FIRMS_KEY") or os.getenv("FIRMS_MAP_KEY") or "").strip()
WHATSAPP_TELEFONE = (
    os.getenv("WHATSAPP_TELEFONE")
    or os.getenv("WHATSAPP_PHONE")
    or os.getenv("CALLMEBOT_PHONE")
    or os.getenv("TELEFONE_WHATSAPP")
    or ""
).strip()
WHATSAPP_APIKEY = (
    os.getenv("WHATSAPP_API_KEY")
    or os.getenv("WHATSAPP_APIKEY")
    or os.getenv("CALLMEBOT_APIKEY")
    or os.getenv("APIKEY_WHATSAPP")
    or ""
).strip()

LATITUDE = float(os.getenv("LATITUDE", "-22.0286"))
LONGITUDE = float(os.getenv("LONGITUDE", "-42.3611"))
RAIO_KM = float(os.getenv("RAIO_KM", "15.0"))
NOME_FAZENDA = os.getenv("NOME_FAZENDA", "Fazenda Piloto - Cordeiro/RJ")


def montar_url(dominio, caminho):
    barra = chr(47)
    return "".join(["https:", barra, barra, dominio.strip(), barra, caminho.strip().lstrip(barra)])


def calcular_distancia_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def avaliar_pulverizacao(temp, umid, vento, prob_chuva):
    if prob_chuva >= 50 or vento > 15 or umid < 45 or temp > 32:
        return "🔴 NÃO APLICAR (Risco de deriva, evaporação ou lavagem)", 10
    if 10 <= vento <= 15 or 45 <= umid < 55 or 28 < temp <= 32 or vento < 3:
        return "🟡 CAUTELA (Atenção ao vento/umidade; preferir horário ameno)", 60
    return "🟢 IDEAL (Condições ótimas para pulverização e adubação)", 100


def avaliar_rebanho(temp, umid):
    itu = 0.8 * temp + (umid / 100.0) * (temp - 14.4) + 46.4
    if itu >= 84:
        return "🚨 ALERTA CRÍTICO DE CALOR: Garantir sombra imediata, água fresca e NÃO movimentar lotes."
    if itu >= 78:
        return "🟠 DESCONFORTO TÉRMICO: Manter rebanho em piquetes com sombra e evitar curral nas horas quentes."
    if itu >= 72:
        return "🟡 ATENÇÃO LEVE: Manter bebedouros cheios e fazer manejo de curral no início da manhã."
    return "🟢 CONFORTO IDEAL: Clima favorável para o rebanho e rotina normal de manejo."


def consultar_clima():
    url = montar_url("://open-meteo.com", "v1/forecast")
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
        "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability,wind_speed_10m",
        "daily": "precipitation_sum,temperature_2m_min",
        "timezone": "America/Sao_Paulo",
        "forecast_days": 2,
    }
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            print(f"Erro Open-Meteo: {r.status_code}")
            return None
        d = r.json()
        atual, hourly, daily = d["current"], d["hourly"], d["daily"]
        temp = float(atual["temperature_2m"])
        umid = float(atual["relative_humidity_2m"])
        vento = float(atual["wind_speed_10m"])
        chuva_hoje = float(daily["precipitation_sum"][0])
        temp_min = float(daily["temperature_2m_min"][0])

        status_pulv, _ = avaliar_pulverizacao(temp, umid, vento, 0)
        melhor_hora, melhor_score = "Sem janela ideal nas próximas 24h", 0
        for i in range(min(24, len(hourly["time"]))):
            ht = float(hourly["temperature_2m"][i])
            hu = float(hourly["relative_humidity_2m"][i])
            hv = float(hourly["wind_speed_10m"][i])
            hp = float(hourly["precipitation_probability"][i] or 0)
            _, score = avaliar_pulverizacao(ht, hu, hv, hp)
            if score > melhor_score:
                melhor_score = score
                hf = hourly["time"][i].split("T")[1]
                melhor_hora = f"{hf} ({ht:.0f}°C | Vento {hv:.0f} km/h | Umid. {hu:.0f}%)"

        return {
            "temp": temp,
            "umid": umid,
            "vento": vento,
            "chuva_hoje": chuva_hoje,
            "status_pulv": status_pulv,
            "melhor_janela": melhor_hora,
            "conforto_gado": avaliar_rebanho(temp, umid),
            "risco_30": (temp >= 30.0 and umid <= 30.0 and vento >= 30.0),
            "risco_geada": (temp_min <= 3.0),
            "temp_min": temp_min,
        }
    except Exception as e:
        print(f"Erro clima: {e}")
        return None


def verificar_focos(chuva_mm=0.0):
    if not NASA_FIRMS_KEY:
        print("NASA_FIRMS_KEY ausente")
        return []
    area = f"{LONGITUDE - 1.0},{LATITUDE - 1.0},{LONGITUDE + 1.0},{LATITUDE + 1.0}"
    url = montar_url("firms.modaps.eosdis.nasa.gov", f"api/area/csv/{NASA_FIRMS_KEY}/VIIRS_SNPP_NRT/{area}/1")
    try:
        r = requests.get(url, timeout=25)
        if r.status_code != 200:
            print(f"Erro NASA: {r.status_code}")
            return []
        df = pd.read_csv(StringIO(r.text))
        if df.empty:
            return []
        focos = []
        for _, row in df.iterrows():
            conf = str(row.get("confidence", "")).lower()
            frp = float(row.get("frp", 0.0))
            if conf == "l" or frp < 2.0 or chuva_mm >= 10.0:
                continue
            dist = calcular_distancia_km(LATITUDE, LONGITUDE, float(row["latitude"]), float(row["longitude"]))
            if dist <= RAIO_KM:
                focos.append({"lat": float(row["latitude"]), "lon": float(row["longitude"]), "dist": round(dist, 2)})
        focos.sort(key=lambda x: x["dist"])
        return focos
    except Exception as e:
        print(f"Erro NASA: {e}")
        return []


def enviar_whatsapp(texto):
    if not WHATSAPP_TELEFONE or not WHATSAPP_APIKEY:
        print("WhatsApp sem credenciais:\n" + texto)
        return
    url = montar_url("://callmebot.com", "whatsapp.php")
    try:
        r = requests.get(url, params={"phone": WHATSAPP_TELEFONE, "text": texto, "apikey": WHATSAPP_APIKEY}, timeout=20)
        print(f"Status WhatsApp: {r.status_code}")
    except Exception as e:
        print(f"Erro WhatsApp: {e}")


def main():
    agora = datetime.now(timezone(timedelta(hours=-3)))
    data_str = agora.strftime("%d/%m/%Y às %H:%M (Brasília)")
    evento = os.getenv("GITHUB_EVENT_NAME", "").strip()
    print(f"VectorX Agro | {data_str} | Evento: {evento}")

    clima = consultar_clima()
    focos = verificar_focos(clima["chuva_hoje"] if clima else 0.0)

    if focos:
        f0 = focos[0]
        link = montar_url("://google.com", "maps") + f"?q={f0['lat']},{f0['lon']}"
        bloco_fogo = f"🚨 *ALERTA DE FOCO DE CALOR!*\n• Total ({RAIO_KM:.0f} km): {len(focos)}\n• Mais próximo: *{f0['dist']} km*\n• Mapa: {link}"
    elif clima and clima["risco_30"]:
        bloco_fogo = "🔥 *RISCO EXTREMO DE INCÊNDIO (30-30-30)*\n• Calor >30°C, umidade <30% e vento >30 km/h."
    else:
        bloco_fogo = f"🛡️ *Área Segura:* Nenhum foco de calor num raio de {RAIO_KM:.0f} km."

    if clima and clima["risco_geada"]:
        bloco_fogo += f"\n❄️ *ALERTA DE GEADA:* Mínima prevista de {clima['temp_min']:.1f}°C!"

    if clima:
        bloco_clima = (
            f"🌡️ *Temp:* {clima['temp']:.1f}°C | 💧 *Umid:* {clima['umid']:.0f}%\n"
            f"💨 *Vento:* {clima['vento']:.1f} km/h | 🌧️ *Chuva:* {clima['chuva_hoje']:.1f} mm\n\n"
            f"🚜 *Adubação e Pulverização:*\n{clima['status_pulv']}\n"
            f"⏰ *Melhor Janela (24h):* {clima['melhor_janela']}\n\n"
            f"🐄 *Manejo do Rebanho:*\n{clima['conforto_gado']}"
        )
    else:
        bloco_clima = "⚠️ *Clima:* Dados temporariamente indisponíveis."

    msg = f"🌱 *VECTORX AGRO — {NOME_FAZENDA.upper()}*\n📅 {data_str}\n\n{bloco_fogo}\n\n{bloco_clima}"

    if focos or (clima and (clima["risco_30"] or clima["risco_geada"])) or agora.hour == 6 or evento == "workflow_dispatch":
        enviar_whatsapp(msg)
    else:
        print("Zero Spam: fora da rotina das 06h e sem emergência.")


if __name__ == "__main__":
    main()
    
