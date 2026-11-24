import os
import math
import requests
import pandas as pd
from io import StringIO
from datetime import datetime, timezone, timedelta

# ==============================================================================
# CONFIGURAÇÕES E SECRETS (Lidas do GitHub Actions)
# ==============================================================================
NASA_FIRMS_KEY = os.getenv("NASA_FIRMS_KEY", "").strip()
WHATSAPP_TELEFONE = os.getenv("WHATSAPP_TELEFONE", "").strip()
WHATSAPP_APIKEY = os.getenv("WHATSAPP_APIKEY", "").strip()

# Coordenadas padrão (Cordeiro/RJ) caso não estejam nas Secrets
LATITUDE = float(os.getenv("LATITUDE", "-22.0286"))
LONGITUDE = float(os.getenv("LONGITUDE", "-42.3611"))
RAIO_KM = float(os.getenv("RAIO_KM", "15.0"))
NOME_FAZENDA = os.getenv("NOME_FAZENDA", "Fazenda Piloto - Cordeiro/RJ")


def montar_url(*partes) -> str:
    return "https://" + "/".join(p.strip("/") for p in partes)


def calcular_distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def avaliar_condicao_pulverizacao(temp: float, umid: float, vento: float, prob_chuva: float) -> tuple[str, int]:
    """Retorna (Status textual, Pontuação de 0 a 100 para achar a melhor janela)."""
    if prob_chuva >= 50 or vento > 15 or umid < 45 or temp > 32:
        return "🔴 NÃO APLICAR (Risco de deriva, evaporação ou lavagem)", 10
    if 10 <= vento <= 15 or 45 <= umid < 55 or 28 < temp <= 32 or vento < 3:
        return "🟡 CAUTELA (Atenção ao vento/umidade; preferir horário ameno)", 60
    return "🟢 IDEAL (Condições ótimas para pulverização e adubação)", 100


def avaliar_conforto_rebanho(temp: float, umid: float) -> str:
    """Calcula ITU internamente e traduz para linguagem prática de campo."""
    itu = 0.8 * temp + (umid / 100.0) * (temp - 14.4) + 46.4
    if itu >= 84:
        return "🚨 ALERTA CRÍTICO DE CALOR: Risco severo ao gado. Garantir sombra imediata, água fresca à vontade e NÃO movimentar lotes."
    if itu >= 78:
        return "🟠 DESCONFORTO TÉRMICO: Queda provável no leite/ganho de peso. Manter rebanho em piquetes com sombra e evitar manejo no curral nas horas quentes."
    if itu >= 72:
        return "🟡 ATENÇÃO LEVE: Calor moderado. Manter bebedouros cheios e fazer manejo de curral preferencialmente no início da manhã."
    return "🟢 CONFORTO IDEAL: Clima favorável para o rebanho e rotina normal de manejo."


def consultar_clima_completo() -> dict | None:
    url = montar_url("://open-meteo.com", "v1", "forecast")
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m",
        "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability,precipitation,wind_speed_10m",
        "daily": "precipitation_sum,temperature_2m_min",
        "timezone": "America/Sao_Paulo",
        "forecast_days": 2,
    }

    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code != 200:
            print(f"⚠️ Erro HTTP Open-Meteo: {resp.status_code}")
            return None

        dados = resp.json()
        atual = dados["current"]
        hourly = dados["hourly"]
        daily = dados["daily"]

        temp = float(atual["temperature_2m"])
        umid = float(atual["relative_humidity_2m"])
        vento = float(atual["wind_speed_10m"])
        chuva_hoje = float(daily["precipitation_sum"][0])
        temp_min_amanha = float(daily["temperature_2m_min"][1])

        # Regra 30-30-30 simultânea (Temp > 30°C, Umidade < 30%, Vento > 30 km/h)
        risco_30_30_30 = (temp >= 30.0) and (umid <= 30.0) and (vento >= 30.0)
        risco_geada = temp_min_amanha <= 3.0

        # Avalia momento atual e busca melhor janela nas próximas 24 horas
        status_pulv, _ = avaliar_condicao_pulverizacao(temp, umid, vento, 0)
        conforto_gado = avaliar_conforto_rebanho(temp, umid)

        melhor_hora = "Sem janela ideal nas próximas 24h"
        melhor_score = 0
        for i in range(min(24, len(hourly["time"]))):
            h_temp = float(hourly["temperature_2m"][i])
            h_umid = float(hourly["relative_humidity_2m"][i])
            h_vento = float(hourly["wind_speed_10m"][i])
            h_prob = float(hourly["precipitation_probability"][i] or 0)
            _, score = avaliar_condicao_pulverizacao(h_temp, h_umid, h_vento, h_prob)
            if score > melhor_score:
                melhor_score = score
                hora_fmt = hourly["time"][i].split("T")[1]
                melhor_hora = f"{hora_fmt} ({h_temp:.0f}°C | Vento {h_vento:.0f} km/h | Umid. {h_umid:.0f}%)"

        return {
            "temp": temp,
            "umid": umid,
            "vento": vento,
            "chuva_hoje": chuva_hoje,
            "status_pulv": status_pulv,
            "melhor_janela": melhor_hora,
            "conforto_gado": conforto_gado,
            "risco_30_30_30": risco_30_30_30,
            "risco_geada": risco_geada,
            "temp_min_amanha": temp_min_amanha,
        }
    except Exception as e:
        print(f"❌ Erro ao consultar Open-Meteo: {e}")
        return None


def verificar_focos_calor(chuva_recente_mm: float = 0.0) -> list[dict]:
    if not NASA_FIRMS_KEY:
        print("⚠️ NASA_FIRMS_KEY não configurada nas Secrets.")
        return []

    # Área aproximada cobrindo Sudeste/Brasil para consulta rápida e leve
    area_coords = f"{LONGITUDE - 1.0},{LATITUDE - 1.0},{LONGITUDE + 1.0},{LATITUDE + 1.0}"
    url = montar_url(
        "firms.modaps.eosdis.nasa.gov",
        "api",
        "area",
        "csv",
        NASA_FIRMS_KEY,
        "VIIRS_SNPP_NRT",
        area_coords,
        "1",
    )

    try:
        resp = requests.get(url, timeout=25)
        if resp.status_code != 200:
            print(f"⚠️ Erro HTTP NASA FIRMS: {resp.status_code}")
            return []

        df = pd.read_csv(StringIO(resp.text))
        if df.empty:
            print("🛰️ Satélite NASA FIRMS: 0 focos na região.")
            return []

        focos = []
        for _, linha in df.iterrows():
            confianca = str(linha.get("confidence", "")).lower()
            frp = float(linha.get("frp", 0.0))

            # Filtros de qualidade definidos na arquitetura VectorX
            if confianca == "l" or frp < 2.0 or chuva_recente_mm >= 10.0:
                continue

            dist = calcular_distancia_km(
                LATITUDE, LONGITUDE, float(linha["latitude"]), float(linha["longitude"])
            )
            if dist <= RAIO_KM:
                focos.append({
                    "lat": float(linha["latitude"]),
                    "lon": float(linha["longitude"]),
                    "distancia_km": round(dist, 2),
                    "frp": frp,
                    "confianca": confianca,
                })

        focos.sort(key=lambda x: x["distancia_km"])
        return focos
    except Exception as e:
        print(f"❌ Erro ao consultar NASA FIRMS: {e}")
        return []


def enviar_whatsapp(mensagem: str) -> bool:
    if not WHATSAPP_TELEFONE or not WHATSAPP_APIKEY:
        print("⚠️ Credenciais do WhatsApp ausentes. Exibindo boletim apenas no log:\n")
        print(mensagem)
        return False

    url = montar_url("://callmebot.com", "whatsapp.php")
    params = {
        "phone": WHATSAPP_TELEFONE,
        "text": mensagem,
        "apikey": WHATSAPP_APIKEY,
    }

    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code == 200:
            print("✅ Boletim enviado com sucesso para o WhatsApp!")
            return True
        print(f"❌ Erro CallMeBot ({resp.status_code}): {resp.text}")
        return False
    except Exception as e:
        print(f"❌ Falha de conexão com WhatsApp: {e}")
        return False


def executar_sentinela():
    fuse_brt = timezone(timedelta(hours=-3))
    agora_brt = datetime.now(fuse_brt)
    data_hora_str = agora_brt.strftime("%d/%m/%Y às %H:%M (Brasília)")
    evento_github = os.getenv("GITHUB_EVENT_NAME", "").strip()

    print(f"🛰️ VectorX Agro | Varredura iniciada em {data_hora_str} | Evento: {evento_github}")

    clima = consultar_clima_completo()
    chuva_atual = clima["chuva_hoje"] if clima else 0.0
    focos = verificar_focos_calor(chuva_atual)

    # 1. Bloco de Fogo e Riscos Extremos
    if focos:
        mais_proximo = focos[0]
        link_fogo = montar_url("://google.com", "maps") + f"?q={mais_proximo['lat']},{mais_proximo['lon']}"
        bloco_fogo = (
            f"🚨 *ALERTA DE FOCO DE CALOR DETECTADO!*\n"
            f"• *Total no raio de {RAIO_KM:.0f} km:* {len(focos)} foco(s)\n"
            f"• *Mais próximo:* a *{mais_proximo['distancia_km']} km* da propriedade\n"
            f"• *Local exato no mapa:* {link_fogo}"
        )
    elif clima and clima.get("risco_30_30_30"):
        bloco_fogo = (
            "🔥 *ATENÇÃO: RISCO EXTREMO DE INCÊNDIO (30-30-30)*\n"
            "• Sem focos por satélite agora, mas calor (>30°C), ar seco (<30%) e vento forte (>30 km/h) favorecem queimadas."
        )
    else:
        bloco_fogo = f"🛡️ *Área Segura:* Nenhum foco de calor detectado por satélite num raio de {RAIO_KM:.0f} km."

    if clima and clima.get("risco_geada"):
        bloco_fogo += f"\n❄️ *ALERTA DE GEADA:* Mínima prevista de {clima['temp_min_amanha']:.1f}°C nas próximas 24h!"

    # 2. Bloco de Clima, Adubação/Pulverização e Rebanho
    if clima:
        bloco_clima = (
            f"🌡️ *Temperatura:* {clima['temp']:.1f}°C  |  💧 *Umidade:* {clima['umid']:.0f}%\n"
            f"💨 *Vento:* {clima['vento']:.1f} km/h  |  🌧️ *Chuva (dia):* {clima['chuva_hoje']:.1f} mm\n\n"
            
