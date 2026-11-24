import os
import math
import time
import requests
import pandas as pd
from io import StringIO
from datetime import datetime, timezone, timedelta

LATITUDE = float((os.getenv("FAZENDA_LAT") or "-22.0286").strip().replace(",", "."))
LONGITUDE = float((os.getenv("FAZENDA_LON") or "-42.3611").strip().replace(",", "."))
RAIO_KM = float((os.getenv("RAIO_ALERTA_KM") or "15.0").strip().replace(",", "."))

_raw_nasa = (os.getenv("NASA_FIRMS_KEY") or "").strip()
NASA_FIRMS_KEY = [p for p in _raw_nasa.split("/") if len(p) == 32][-1] if "/" in _raw_nasa else _raw_nasa.lstrip(":")

WHATSAPP_PHONE = (os.getenv("WHATSAPP_PHONE") or "").strip()
WHATSAPP_APIKEY = (os.getenv("WHATSAPP_APIKEY") or "").strip()


def criar_link(partes_dominio, *pedacos):
    barra = "/"
    return "https:" + barra + barra + ".".join(partes_dominio) + barra + barra.join(pedacos)


def calcular_distancia_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def direcao_propagacao_vento(graus_origem):
    destino = (graus_origem + 180.0) % 360.0
    pontos = ["Norte", "Nordeste", "Leste", "Sudeste", "Sul", "Sudoeste", "Oeste", "Noroeste"]
    return pontos[int((destino + 22.5) // 45) % 8]


def avaliar_pulverizacao(temp, umid, vento, chuva_mm, prob_chuva):
    if chuva_mm > 0.2 or prob_chuva >= 55 or vento > 15 or umid < 45 or temp > 32:
        return "🔴 NÃO APLICAR (Risco de chuva, deriva ou evaporação)", 10
    if prob_chuva >= 30 or umid > 90 or vento >= 10 or umid < 55 or temp > 28 or vento < 3:
        return "🟡 CAUTELA (Orvalho/umidade alta, vento ou chance de pancada)", 60
    return "🟢 IDEAL (Janela ótima para pulverização e adubação)", 100


def avaliar_rebanho(temp, umid):
    itu = 0.8 * temp + (umid / 100.0) * (temp - 14.4) + 46.4
    if itu >= 84:
        return "🚨 *Rebanho:* Calor crítico! Sombra imediata, água fresca e zero manejo."
    if itu >= 78:
        return "🟠 *Rebanho:* Desconforto térmico. Manter na sombra e evitar curral nas horas quentes."
    if itu >= 72:
        return "🟡 *Rebanho:* Atenção leve. Preferir manejo no início da manhã."
    return "🟢 *Rebanho:* Conforto térmico ideal."


def enviar_alerta_whatsapp(mensagem):
    if not WHATSAPP_PHONE or not WHATSAPP_APIKEY:
        print("⚠️ Credenciais WhatsApp ausentes:\n" + mensagem)
        return
    url = criar_link(["api", "callmebot", "com"], "whatsapp.php")
    try:
        resp = requests.get(url, params={"phone": WHATSAPP_PHONE, "text": mensagem, "apikey": WHATSAPP_APIKEY}, timeout=20)
        print("✅ WhatsApp enviado!" if resp.status_code == 200 else f"❌ Erro WhatsApp: {resp.status_code}")
    except Exception as e:
        print(f"❌ Falha WhatsApp: {e}")


def verificar_clima(agora_brt):
    base = criar_link(["api", "open-meteo", "com"], "v1", "forecast")
    url = f"{base}?latitude={LATITUDE}&longitude={LONGITUDE}&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation&hourly=temperature_2m,relative_humidity_2m,precipitation_probability,precipitation,wind_speed_10m&timezone=America/Sao_Paulo&forecast_days=2"
    dados = None
    for t in range(2):
        try:
            resp = requests.get(url, headers={"User-Agent": "VectorXAgro/1.0"}, timeout=25)
            if resp.status_code == 200 and resp.text.strip():
                dados = resp.json()
                break
        except Exception as e:
            print(f"⚠️ Tentativa {t + 1} Open-Meteo: {e}")
            time.sleep(2)

    if not dados:
        return [], "Dados climáticos indisponíveis no momento.", None

    atual, hourly = dados.get("current", {}), dados.get("hourly", {})
    temp = float(atual.get("temperature_2m", 0))
    umid = float(atual.get("relative_humidity_2m", 0))
    vento = float(atual.get("wind_speed_10m", 0))
    dir_vento = float(atual.get("wind_direction_10m", 0))
    chuva = float(atual.get("precipitation", 0))

    hora_str = agora_brt.strftime("%Y-%m-%dT%H:00")
    tempos_h = hourly.get("time", [])
    idx = tempos_h.index(hora_str) if hora_str in tempos_h else 0
    probs_h = hourly.get("precipitation_probability", [])
    prob_agora = float(probs_h[idx] or 0) if idx < len(probs_h) else 0.0

    alertas = []
    if temp >= 30 and umid <= 30 and vento >= 30:
        alertas.append("🚨 *RISCO EXTREMO DE INCÊNDIO (Regra 30-30-30 atingida!)*")
    elif umid < 25:
        alertas.append("⚠️ *Umidade relativa crítica (< 25%)*")
    if vento > 40:
        alertas.append(f"💨 *Rajadas de vento fortes ({vento:.0f} km/h)*")

    status_pulv, _ = avaliar_pulverizacao(temp, umid, vento, chuva, prob_agora)
    gado = avaliar_rebanho(temp, umid)

    melhor_hora, melhor_score = "Sem janela ideal nas próximas 24h", 0
    for i in range(idx + 1, min(idx + 25, len(tempos_h))):
        ht = float(hourly["temperature_2m"][i])
        hu = float(hourly["relative_humidity_2m"][i])
        hv = float(hourly["wind_speed_10m"][i])
        hc = float(hourly["precipitation"][i] or 0)
        hp = float(hourly["precipitation_probability"][i] or 0)
        _, score = avaliar_pulverizacao(ht, hu, hv, hc, hp)
        if score > melhor_score:
            melhor_score = score
            melhor_hora = f"{tempos_h[i].split('T')} ({ht:.0f}°C | Vento {hv:.0f} km/h | Umid. {hu:.0f}%)"

    destino_vento = direcao_propagacao_vento(dir_vento)
    resumo = (
        f"🌡️ Temp: {temp:.1f}°C | 💧 Umid: {umid:.0f}%\n"
        f"💨 Vento: {vento:.1f} km/h (indo p/ {destino_vento})\n"
        f"🌧️ Chuva: {chuva:.1f} mm (Chance: {prob_agora:.0f}%)\n\n"
        f"🚜 *Pulverização/Adubação:*\n{status_pulv}\n"
        f"⏰ *Melhor Janela (24h):* {melhor_hora}\n\n"
        f"{gado}"
    )
    return alertas, resumo, {"velocidade": vento, "destino": destino_vento}


def verificar_focos_calor():
    if not NASA_FIRMS_KEY:
        return []
    delta = RAIO_KM / 111.0
    area = f"{LONGITUDE - delta:.4f},{LATITUDE - delta:.4f},{LONGITUDE + delta:.4f},{LATITUDE + delta:.4f}"
    url = criar_link(["firms", "modaps", "eosdis", "nasa", "gov"], "api", "area", "csv", NASA_FIRMS_KEY, "VIIRS_SNPP_NRT", area, "1")
    try:
        resp = requests.get(url, headers={"User-Agent": "VectorXAgro/1.0"}, timeout=25)
        if resp.status_code != 200:
            return []
        df = pd.read_csv(StringIO(resp.text))
        if df.empty:
            print("🛰️ Satélite NASA FIRMS: 0 focos na área.")
            return []
        focos = []
        for _, row in df.iterrows():
            if str(row.get("confidence", "")).lower() == "l" or float(row.get("frp", 0.0)) < 2.0:
                continue
            dist = calcular_distancia_km(LATITUDE, LONGITUDE, float(row["latitude"]), float(row["longitude"]))
            if dist <= RAIO_KM:
                focos.append({"lat": float(row["latitude"]), "lon": float(row["longitude"]), "distancia_km": round(dist, 2)})
        focos.sort(key=lambda x: x["distancia_km"])
        return focos
    except Exception as e:
        print(f"Erro NASA: {e}")
        return []


def executar_sentinela():
    agora_brt = datetime.now(timezone(timedelta(hours=-3)))
    agora_str = agora_brt.strftime("%d/%m/%Y %H:%M (Brasília)")
    evento = (os.getenv("GITHUB_EVENT_NAME") or "").strip()
    print(f"🛰️ Varredura VectorX Agro ({agora_str}) | Evento: {evento}")

    alertas_clima, resumo_clima, info_vento = verificar_clima(agora_brt)
    focos = verificar_focos_calor()
    mensagens = []

    if focos:
        mais_perto = focos
        mensagens.append(f"🔥 *ALERTA DE QUEIMADA DETECTADO!*\n• *Mais próximo:* a *{mais_perto['distancia_km']} km* da propriedade\n• *Total ({RAIO_KM:.0f} km):* {len(focos)} foco(s)")
        if info_vento:
            mensagens.append(f"• *Propagação do Vento:* {info_vento['velocidade']:.1f} km/h empurrando para *{info_vento['destino']}*")
        base_mapa = criar_link(["www", "google", "com"], "maps")
        for i, f in enumerate(focos[:3], 1):
            mensagens.append(f"• *Mapa Foco {i} ({f['distancia_km']} km):* {base_mapa}?q={f['lat']},{f['lon']}")

    if alertas_clima:
        mensagens.extend(alertas_clima)

    if mensagens or agora_brt.hour == 6 or evento == "workflow_dispatch":
        cabecalho = "\n".join(mensagens) if mensagens else f"🛡️ *Área Segura:* Nenhum foco de calor no raio de {RAIO_KM:.0f} km."
        texto = f"🚜 *VECTORX AGRO - SENTINELA 24/7*\n🕒 {agora_str}\n\n{cabecalho}\n\n📊 *Condições Atuais & Manejo:*\n{resumo_clima}"
        enviar_alerta_whatsapp(texto)
    else:
        print("✅ Zero Spam: sem emergências e fora das 06h.")


if __name__ == "__main__":
    executar_sentinela()
        
