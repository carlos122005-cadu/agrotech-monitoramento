import os
import requests
import pandas as pd
from io import StringIO
from datetime import datetime, timezone
from urllib.parse import urlunparse

# =====================================================================
# CONFIGURAÇÕES
# =====================================================================
LATITUDE = float((os.getenv("FAZENDA_LAT") or "-22.0286").strip().replace(",", "."))
LONGITUDE = float((os.getenv("FAZENDA_LON") or "-42.3611").strip().replace(",", "."))
RAIO_KM = float((os.getenv("RAIO_ALERTA_KM") or "15.0").strip().replace(",", "."))

NASA_FIRMS_KEY = (os.getenv("NASA_FIRMS_KEY") or "").strip().lstrip(":")
WHATSAPP_PHONE = (os.getenv("WHATSAPP_PHONE") or "").strip()
WHATSAPP_APIKEY = (os.getenv("WHATSAPP_APIKEY") or "").strip()


def montar_url(dominio, *partes):
    dominio_limpo = dominio.strip(".")
    caminho = chr(47) + chr(47).join(str(p).strip(chr(47)) for p in partes)
    return urlunparse(("https", dominio_limpo, caminho, "", "", ""))


def enviar_alerta_whatsapp(mensagem: str):
    if not WHATSAPP_PHONE or not WHATSAPP_APIKEY:
        print("⚠️ Credenciais do WhatsApp não configuradas. Mensagem apenas no log:")
        print(mensagem)
        return

    url = montar_url("api." + "callmebot.com", "whatsapp.php")
    params = {
        "phone": WHATSAPP_PHONE,
        "text": mensagem,
        "apikey": WHATSAPP_APIKEY
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            print("✅ Alerta enviado com sucesso no WhatsApp!")
        else:
            print(f"❌ Erro ao enviar WhatsApp: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"❌ Falha de conexão com API do WhatsApp: {e}")


def verificar_clima():
    url = montar_url("api." + "open-meteo.com", "v1", "forecast")
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
        "timezone": "America" + chr(47) + "Sao_Paulo"
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code != 200:
            print(f"⚠️ Erro HTTP Open-Meteo: {resp.status_code}")
            return [], "Dados climáticos indisponíveis no momento."

        dados = resp.json()
        atual = dados.get("current", {})
        temp = atual.get("temperature_2m", 0)
        umid = atual.get("relative_humidity_2m", 0)
        vento = atual.get("wind_speed_10m", 0)
        chuva = atual.get("precipitation", 0)

        alertas_clima = []
        if temp > 30 and umid < 30 and vento > 30:
            alertas_clima.append("🚨 *RISCO EXTREMO DE INCÊNDIO (Regra 30-30-30 atingida!)*")
        elif umid < 25:
            alertas_clima.append("⚠️ *Umidade relativa crítica (< 25%)*")

        if vento > 40:
            alertas_clima.append(f"💨 *Rajadas de vento fortes ({vento} km/h)*")

        resumo = (
            f"🌡️ Temp: {temp}°C | 💧 Umid: {umid}%\n"
            f"💨 Vento: {vento} km/h | 🌧️ Chuva: {chuva} mm"
        )
        return alertas_clima, resumo
    except Exception as e:
        print(f"Erro ao consultar clima: {e}")
        return [], "Dados climáticos indisponíveis no momento."


def verificar_focos_calor():
    if not NASA_FIRMS_KEY:
        print("⚠️ NASA_FIRMS_KEY não configurada.")
        return []

    delta = RAIO_KM / 111.0
    area = f"{LONGITUDE - delta:.4f},{LATITUDE - delta:.4f},{LONGITUDE + delta:.4f},{LATITUDE + delta:.4f}"
    url = montar_url(
        "firms.modaps.eosdis.nasa.gov",
        "api", "area", "csv",
        NASA_FIRMS_KEY,
        "VIIRS_SNPP_NRT",
        area,
        "1"
    )

    try:
        resp = requests.get(url, timeout=25)
        if resp.status_code != 200:
            print(f"⚠️ Erro HTTP NASA FIRMS ({resp.status_code}): {resp.text[:120]}")
            return []

        df = pd.read_csv(StringIO(resp.text))
        if df.empty:
            print("🛰️ Satélite NASA FIRMS consultado com sucesso: 0 focos na área.")
            return []

        focos = []
        for _, row in df.iterrows():
            focos.append({
                "lat": row["latitude"],
                "lon": row["longitude"],
                "brilho": row["bright_ti4"],
                "confianca": row["confidence"],
                "hora_utc": row["acq_time"]
            })
        return focos
    except Exception as e:
        print(f"Erro ao consultar NASA FIRMS: {e}")
        return []


def executar_sentinela():
    agora = datetime.now(timezone.utc).strftime("%d" + chr(47) + "%m" + chr(47) + "%Y %H:%M UTC")
    print(f"🛰️ Iniciando varredura VectorX Agro ({agora})...")

    alertas_clima, resumo_clima = verificar_clima()
    focos = verificar_focos_calor()

    mensagens = []

    if focos:
        mensagens.append(f"🔥 *ALERTA DE QUEIMADA DETECTADO!* ({len(focos)} foco(s) no raio de {RAIO_KM} km)")
        for i, f in enumerate(focos[:5], 1):
            link_maps = montar_url("://google.com", "") + f"?q={f['lat']},{f['lon']}"
            mensagens.append(f"• Foco {i} (Confiança: {f['confianca']}): {link_maps}")

    if alertas_clima:
        mensagens.extend(alertas_clima)

    if mensagens:
        texto_final = (
            f"🚜 *VECTORX AGRO - SENTINELA 24" + chr(47) + "7*\n"
            f"🕒 {agora}\n\n"
            + "\n".join(mensagens)
            + f"\n\n📊 *Condições Atuais:*\n{resumo_clima}"
        )
        enviar_alerta_whatsapp(texto_final)
    else:
        print("✅ Nenhum foco de calor ou risco climático crítico detectado.")
        print(resumo_clima)


if __name__ == "__main__":
    executar_sentinela()
                
