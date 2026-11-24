import os
import math
import time
import requests
import pandas as pd
from io import StringIO
from datetime import datetime, timezone, timedelta

# =====================================================================
# CONFIGURAÇÕES (Nomes exatos das suas Secrets)
# =====================================================================
LATITUDE = float((os.getenv("FAZENDA_LAT") or "-22.0286").strip().replace(",", "."))
LONGITUDE = float((os.getenv("FAZENDA_LON") or "-42.3611").strip().replace(",", "."))
RAIO_KM = float((os.getenv("RAIO_ALERTA_KM") or "15.0").strip().replace(",", "."))

_raw_nasa = (os.getenv("NASA_FIRMS_KEY") or "").strip()
NASA_FIRMS_KEY = [p for p in _raw_nasa.split("/") if len(p) == 32][-1] if "/" in _raw_nasa else _raw_nasa.lstrip(":")

WHATSAPP_PHONE = (os.getenv("WHATSAPP_PHONE") or "").strip()
WHATSAPP_APIKEY = (os.getenv("WHATSAPP_APIKEY") or "").strip()


def criar_link(partes_dominio, *pedacos):
    barra = "/"
    dominio = ".".join(partes_dominio)
    return "https:" + barra + barra + dominio + barra + barra.join(pedacos)


def calcular_distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def direcao_propagacao_vento(graus_origem: float) -> str:
    """Converte a direção de origem do vento (+180°) para onde o fogo/fumaça vai."""
    destino = (graus_origem + 180.0) % 360.0
    pontos = ["Norte", "Nordeste", "Leste", "Sudeste", "Sul", "Sudoeste", "Oeste", "Noroeste"]
    indice = int((destino + 22.5) // 45) % 8
    return pontos[indice]


def avaliar_pulverizacao(temp: float, umid: float, vento: float, chuva_mm: float, prob_chuva: float) -> tuple[str, int]:
    if chuva_mm > 0.2 or prob_chuva >= 55 or vento > 15 or umid < 45 or temp > 32:
        return "🔴 NÃO APLICAR (Risco de chuva, deriva ou evaporação)", 10
    if prob_chuva >= 30 or umid > 90 or vento >= 10 or umid < 55 or temp > 28 or vento < 3:
        return "🟡 CAUTELA (Orvalho/umidade alta, vento ou chance de pancada)", 60
    return "🟢 IDEAL (Janela ótima para pulverização e adubação)", 100


def avaliar_rebanho(temp: float, umid: float) -> str:
    itu = 0.8 * temp + (umid / 100.0) * (temp - 14.4) + 46.4
    if itu >= 84:
        return "🚨 *Rebanho:* Calor crítico! Sombra imediata, água fresca e zero manejo."
    if itu >= 78:
        return "🟠 *Rebanho:* Desconforto térmico. Manter na sombra e evitar curral nas horas quentes."
    if itu >= 72:
        return "🟡 *Rebanho:* Atenção leve. Preferir manejo no início da manhã."
    return "🟢 *Rebanho:* Conforto térmico ideal."


def enviar_alerta_whatsapp(mensagem: str):
    if not WHATSAPP_PHONE or not WHATSAPP_APIKEY:
        print("⚠️ Credenciais do WhatsApp não configuradas. Mensagem apenas no log:")
        print(mensagem)
        return

    url = criar_link(["api", "callmebot", "com"], "whatsapp.php")
    params = {
        "phone": WHATSAPP_PHONE,
        "text": mensagem,
        "apikey": WHATSAPP_APIKEY
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code == 200:
            print("✅ Alerta enviado com sucesso no WhatsApp!")
        else:
            print(f"❌ Erro ao enviar WhatsApp: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"❌ Falha de conexão com API do WhatsApp: {e}")


def verificar_clima(agora_brt: datetime):
    base_clima = criar_link(["api", "open-meteo", "com"], "v1", "forecast")
    url = (
        f"{base_clima}"
        f"?latitude={LATITUDE}&longitude={LONGITUDE}"
        f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation"
        f"&hourly=temperature_2m,relative_humidity_2m,precipitation_probability,precipitation,wind_speed_10m"
        f"&timezone=America/Sao_Paulo&forecast_days=2"
    )

    dados = None
    for tentativa in range(2):
        try:
            resp = requests.get(url, headers={"User-Agent": "VectorXAgro/1.0"}, timeout=25)
            if resp.status_code == 200 and resp.text.strip():
                dados = resp.json()
                break
            print(f"⚠️ Resposta inválida Open-Meteo (Tentativa {tentativa + 1}): {resp.status_code}")
        except Exception as e:
            print(f"⚠️ Oscilação Open-Meteo (Tentativa {tentativa + 1}): {e}")
            time.sleep(2)

    if not dados:
        return [], "Dados climáticos indisponíveis no momento.", None

    atual = dados.get("current", {})
    hourly = dados.get("hourly", {})

    temp = float(atual.get("temperature_2m", 0))
    umid = float(atual.get("relative_humidity_2m", 0))
    vento = float(atual.get("wind_speed_10m", 0))
    dir_vento = float(atual.get("wind_direction_10m", 0))
    chuva = float(atual.get("precipitation", 0))

    # Pega probabilidade de chuva da hora atual no array hourly
    hora_atual_str = agora_brt.strftime("%Y-%m-%dT%H:00")
    tempos_h = hourly.get("time", [])
    idx_atual = tempos_h.index(hora_atual_str) if hora_atual_str in tempos_h else 0
    probs_h = hourly.get("precipitation_probability", [])
    prob_chuva_agora = float(probs_h[idx_atual] or 0) if idx_atual < len(probs_h) else 0.0

    alertas_clima = []
    if temp >= 30 and umid <= 30 and vento >= 30:
        alertas_clima.append("🚨 *RISCO EXTREMO DE INCÊNDIO (Regra 30-30-30 atingida!)*")
    elif umid < 25:
        alertas_clima.append("⚠️ *Umidade relativa crítica (< 25%)*")

    if vento > 40:
        alertas_clima.append(f"💨 *Rajadas de vento fortes ({vento:.0f} km/h)*")

    status_pulv, _ = avaliar_pulverizacao(temp, umid, vento, chuva, prob_chuva_agora)
    gado = avaliar_rebanho(temp, umid)

    # Busca melhor janela nas próximas 24 horas a partir da hora atual
    melhor_hora = "Sem janela ideal nas próximas 24h"
    melhor_score = 0
    for i in range(idx_atual + 1, min(idx_atual + 25, len(tempos_h))):
        ht = float(hourly["temperature_2m"][i])
        hu = float(hourly["relative_humidity_2m"][i])
        hv = float(hourly["wind_speed_10m"][i])
        hc = float(hourly["precipitation"][i] or 0)
        hp = float(hourly["precipitation_probability"][i] or 0)
        _, score = avaliar_pulverizacao(ht, hu, hv, hc, hp)
        if score > melhor_score:
            melhor_score = score
            hora_limpa = tempos_h[i].split("T")
            melhor_hora = f"{hora_limpa} ({ht:.0f}°C | Vento {hv:.0f} km/h | Umid. {hu:.0f}%)"

    para_onde_vai = direcao_propagacao_vento(dir_vento)
    info_vento = {"velocidade": vento, "destino": para_onde_vai}

    resumo = (
        f"🌡️ Temp: {temp:.1f}°C | 💧 Umid: {umid:.0f}%\n"
        f"💨 Vento: {vento:.1f} km/h (indo p/ {para_onde_vai})\n"
        f"🌧️ Chuva: {chuva:.1f} mm (Chance: {prob_chuva_agora:.0f}%)\n\n"
        f"🚜 *Pulverização/Adubação:*\n{status_pulv}\n"
        f"⏰ *Melhor Janela (24h):* {melhor_hora}\n\n"
        f"{gado}"
    )
    return alertas_clima, resumo, info_vento


def verificar_focos_calor():
    if not NASA_FIRMS_KEY:
        print("⚠️ NASA_FIRMS_KEY não configurada. Pulando checagem de satélite.")
        return []

    delta = RAIO_KM / 111.0
    area = f"{LONGITUDE - delta:.4f},{LATITUDE - delta:.4f},{LONGITUDE + delta:.4f},{LATITUDE + delta:.4f}"
    url = criar_link(["firms", "modaps", "eosdis", "nasa", "gov"], "api", "area", "csv", NASA_FIRMS_KEY, "VIIRS_SNPP_NRT", area, "1")

    try:
        resp = requests.get(url, headers={"User-Agent": "VectorXAgro/1.0"}, timeout=25)
        if resp.status_code != 200:
            print(f"⚠️ Erro HTTP NASA FIRMS (Status {resp.status_code}): {resp.text[:150]}")
            return []

        df = pd.read_csv(StringIO(resp.text))
        if df.empty:
            print("🛰️ Satélite NASA FIRMS consultado com sucesso: 0 focos na área.")
            return []

        focos = []
        for _, row in df.iterrows():
            conf = str(row.get("confidence", "")).lower()
            frp = float(row.get("frp", 0.0))
            if conf == "l" or frp < 2.0:
                continue

            f_lat = float(row["latitude"])
            f_lon = float(row["longitude"])
            dist = calcular_distancia_km(LATITUDE, LONGITUDE, f_lat, f_lon)

            if dist <= RAIO_KM:
                focos.append({
                    "lat": f_lat,
                    "lon": f_lon,
                    "distancia_km": round(dist, 2),
                    "frp": frp,
                    "confianca": row["confidence"],
                })

        focos.sort(key=lambda x: x["distancia_km"])
        return focos
    except Exception as e:
        print(f"Erro ao consultar NASA FIRMS: {e}")
        return []


def executar_sentinela():
    fuso_brt = timezone(timedelta(hours=-3))
    agora_brt = datetime.now(fuso_brt)
    agora_str = agora_brt.strftime("%d/%m/%Y %H:%M (Brasília)")
    evento = (os.getenv("GITHUB_EVENT_NAME") or "").strip()

    print(f"🛰️ Iniciando varredura VectorX Agro ({agora_str}) | Evento: {evento}...")

    alertas_clima, resumo_clima, info_vento = verificar_clima(agora_brt)
    focos = verificar_focos_calor()

    mensagens = []

    if focos:
        mais_perto = focos
        mensagens.append(
            f"🔥 *ALERTA DE QUEIMADA DETECTADO!*\n"
            f"• *Foco mais próximo:* a *{mais_perto['distancia_km']} km* da propriedade\n"
            f"• *Total no raio de {RAIO_KM:.0f} km:* {len(focos)} foco(s)"
        )
        if info_vento:
            mensagens.append(
                f"• *Propagação do Vento:* {info_vento['velocidade']:.1f} km/h empurrando para *{info_vento['destino']}*"
            )
        base_mapa = criar_link(["www", "google", "com"], "maps")
        for i, f in enumerate(focos[:3], 1):
