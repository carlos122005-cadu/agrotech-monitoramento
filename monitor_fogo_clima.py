import os
import math
import time
import requests
import pandas as pd
from io import StringIO
from datetime import datetime, timezone, timedelta

FALLBACK_LAT = float((os.getenv("FAZENDA_LAT") or "-22.0286").strip().replace(",", "."))
FALLBACK_LON = float((os.getenv("FAZENDA_LON") or "-42.3611").strip().replace(",", "."))
FALLBACK_RAIO = float((os.getenv("RAIO_ALERTA_KM") or "15.0").strip().replace(",", "."))

_raw_nasa = (os.getenv("NASA_FIRMS_KEY") or "").strip()
NASA_FIRMS_KEY = [p for p in _raw_nasa.split("/") if len(p) == 32][-1] if "/" in _raw_nasa else _raw_nasa.lstrip(":")

FALLBACK_PHONE = (os.getenv("WHATSAPP_PHONE") or "").strip()
FALLBACK_APIKEY = (os.getenv("WHATSAPP_APIKEY") or "").strip()

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SUPABASE_KEY = (os.getenv("SUPABASE_KEY") or "").strip()


def criar_link(partes_dominio, *pedacos):
    barra = "/"
    return "https:" + barra + barra + ".".join(partes_dominio) + barra + barra.join(pedacos)


def headers_supabase():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def buscar_fazendas_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    url = f"{SUPABASE_URL}/rest/v1/rpc/listar_fazendas_ativas"
    try:
        r = requests.post(url, headers=headers_supabase(), json={}, timeout=20)
        if r.status_code != 200:
            print(f"⚠️ Erro Supabase listar fazendas ({r.status_code}): {r.text[:150]}")
            return []
        lista = []
        for item in r.json():
            d = item.get("dados") or {}
            nome = d.get("nome_fazenda") or d.get("nome") or d.get("propriedade") or "Fazenda VectorX"
            tel = str(d.get("whatsapp") or d.get("telefone") or FALLBACK_PHONE).strip()
            key = str(d.get("apikey_whatsapp") or d.get("apikey") or FALLBACK_APIKEY).strip()
            lista.append({
                "id": d.get("id"),
                "nome": nome,
                "lat": float(item.get("lat") or FALLBACK_LAT),
                "lon": float(item.get("lon") or FALLBACK_LON),
                "raio_km": float(d.get("raio_km") or FALLBACK_RAIO),
                "whatsapp": tel,
                "apikey": key,
            })
        return lista
    except Exception as e:
        print(f"⚠️ Falha conexão Supabase: {e}")
        return []


def alerta_ja_enviado_24h(fazenda_id, tipo_alerta):
    if not SUPABASE_URL or not SUPABASE_KEY or not fazenda_id:
        return False
    limite = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    url = f"{SUPABASE_URL}/rest/v1/alertas_enviados"
    params = {
        "select": "id",
        "fazenda_id": f"eq.{fazenda_id}",
        "tipo_alerta": f"eq.{tipo_alerta}",
        "created_at": f"gte.{limite}",
    }
    try:
        r = requests.get(url, headers=headers_supabase(), params=params, timeout=15)
        return r.status_code == 200 and len(r.json()) > 0
    except Exception:
        return False


def registrar_alerta_e_limpar(fazenda_id, tipo_alerta):
    if not SUPABASE_URL or not SUPABASE_KEY or not fazenda_id:
        return
    url = f"{SUPABASE_URL}/rest/v1/alertas_enviados"
    try:
        requests.post(url, headers=headers_supabase(), json={"fazenda_id": fazenda_id, "tipo_alerta": tipo_alerta}, timeout=15)
        limite_48h = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        requests.delete(url, headers=headers_supabase(), params={"created_at": f"lt.{limite_48h}"}, timeout=15)
    except Exception as e:
        print(f"⚠️ Aviso log Supabase: {e}")


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


def enviar_alerta_whatsapp(phone, apikey, mensagem):
    if not phone or not apikey:
        print("⚠️ Credenciais WhatsApp ausentes:\n" + mensagem)
        return False
    url = criar_link(["api", "callmebot", "com"], "whatsapp.php")
    try:
        resp = requests.get(url, params={"phone": phone, "text": mensagem, "apikey": apikey}, timeout=20)
        print("✅ WhatsApp enviado!" if resp.status_code == 200 else f"❌ Erro WhatsApp: {resp.status_code}")
        return resp.status_code == 200
    except Exception as e:
        print(f"❌ Falha WhatsApp: {e}")
        return False


def verificar_clima(lat, lon, agora_brt):
    base = criar_link(["api", "open-meteo", "com"], "v1", "forecast")
    url = f"{base}?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation&hourly=temperature_2m,relative_humidity_2m,precipitation_probability,precipitation,wind_speed_10m&timezone=America/Sao_Paulo&forecast_days=2"
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
            melhor_hora = f"{tempos_h[i].replace('T', ' às ')} ({ht:.0f}°C | Vento {hv:.0f} km/h | Umid. {hu:.0f}%)"

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


def baixar_focos_regiao(fazendas):
    if not NASA_FIRMS_KEY or not fazendas:
        return pd.DataFrame()
    lats = [f["lat"] for f in fazendas]
    lons = [f["lon"] for f in fazendas]
    area = f"{min(lons) - 1.0:.4f},{min(lats) - 1.0:.4f},{max(lons) + 1.0:.4f},{max(lats) + 1.0:.4f}"
    url = criar_link(["firms", "modaps", "eosdis", "nasa", "gov"], "api", "area", "csv", NASA_FIRMS_KEY, "VIIRS_SNPP_NRT", area, "1")
    try:
        resp = requests.get(url, headers={"User-Agent": "VectorXAgro/1.0"}, timeout=25)
        if resp.status_code != 200:
            return pd.DataFrame()
        df = pd.read_csv(StringIO(resp.text))
        print(f"🛰️ Satélite NASA FIRMS: {len(df)} leitura(s) bruta(s) na região.")
        return df
    except Exception as e:
        print(f"Erro NASA: {e}")
        return pd.DataFrame()


def filtrar_focos_fazenda(df_focos, lat, lon, raio_km):
    if df_focos.empty:
        return []
    focos = []
    for _, row in df_focos.iterrows():
        if str(row.get("confidence", "")).lower() == "l" or float(row.get("frp", 0.0)) < 2.0:
            continue
                      
