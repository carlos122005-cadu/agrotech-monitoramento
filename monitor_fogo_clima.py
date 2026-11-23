import os
import math
import requests
import pandas as pd
from io import StringIO
from datetime import datetime, timezone, timedelta
from urllib.parse import urlunparse

# =====================================================================
# CONFIGURAÇÕES DA PROPRIEDADE
# =====================================================================
NOME_FAZENDA = (os.getenv("NOME_FAZENDA") or "Fazenda Piloto Cordeiro").strip()
LATITUDE = float((os.getenv("FAZENDA_LAT") or "-22.0286").strip().replace(",", "."))
LONGITUDE = float((os.getenv("FAZENDA_LON") or "-42.3611").strip().replace(",", "."))
RAIO_KM = float((os.getenv("RAIO_ALERTA_KM") or "15.0").strip().replace(",", "."))

NASA_FIRMS_KEY = (os.getenv("NASA_FIRMS_KEY") or "").strip().lstrip(":")
WHATSAPP_PHONE = (os.getenv("WHATSAPP_PHONE") or "").strip()
WHATSAPP_APIKEY = (os.getenv("WHATSAPP_APIKEY") or "").strip()

# Horários do boletim diário de rotina (Horário de Brasília: 06h e 17h)
HORARIOS_BOLETIM = [6, 17]


def montar_url(dominio, *partes):
    dominio_limpo = dominio.strip(".")
    caminho = chr(47) + chr(47).join(str(p).strip(chr(47)) for p in partes)
    return urlunparse(("https", dominio_limpo, caminho, "", "", ""))


def direcao_cardeal(graus):
    pontos = [
        "Norte", "Nordeste", "Leste", "Sudeste",
        "Sul", "Sudoeste", "Oeste", "Noroeste"
    ]
    idx = round(graus / 45) % 8
    return pontos[idx]


def calcular_distancia_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def avaliar_conforto_gado(temp, umid):
    # Cálculo interno do índice térmico traduzido para linguagem simples do campo
    itu = 0.8 * temp + (umid / 100.0) * (temp - 14.4) + 46.4
    if itu < 72:
        return "🟢 *Confortável* — Clima agradável, sem estresse por calor para o rebanho."
    elif itu < 79:
        return "🟡 *Atenção Leve* — Calor moderado; manter água limpa e sombra disponível."
    elif itu < 89:
        return "🟠 *Alerta de Calor* — Estresse térmico; evitar manejo pesado no curral agora."
    else:
        return "🔴 *Perigo de Calor Extremo* — Risco alto para o gado; priorizar sombra e hidratação."


def avaliar_pulverizacao_agora(temp, umid, vento, chuva):
    if chuva > 0.2:
        return (
            "🔴 *NÃO APLICAR AGORA*\n"
            "• *Motivo:* Há chuva no momento — o produto será lavado da folha."
        )
    if vento > 15:
        return (
            "🔴 *NÃO APLICAR AGORA*\n"
            f"• *Motivo:* Vento forte ({vento:.1f} km/h) — alto risco de deriva e perda do produto."
        )
    if vento < 3:
        return (
            "🔴 *NÃO APLICAR AGORA*\n"
            f"• *Motivo:* Vento parado ({vento:.1f} km/h) — risco de inversão térmica (gotas ficam suspensas no ar)."
        )
    if umid < 45 or temp > 32:
        return (
            "🔴 *NÃO APLICAR AGORA*\n"
            f"• *Motivo:* Ar muito seco ({umid}%) ou quente ({temp:.1f}°C) — gota evapora antes de agir."
        )
    if (10 < vento <= 15) or (45 <= umid < 55) or (29 < temp <= 32):
        return (
            "🟡 *APLICAR COM CAUTELA*\n"
            "• *Motivo:* Condição no limite — usar ponta de gota grossa e redobrar atenção na deriva."
        )
    return (
        "🟢 *CONDIÇÃO IDEAL PARA APLICAR*\n"
        "• *Motivo:* Temperatura, umidade e vento dentro da faixa segura para boa cobertura."
    )


def encontrar_melhor_janela(hourly):
    tempos = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    umids = hourly.get("relative_humidity_2m", [])
    ventos = hourly.get("wind_speed_10m", [])
    chuvas = hourly.get("precipitation", [])

    horas_boas = []
    for i in range(min(24, len(tempos))):
        t = temps[i]
        u = umids[i]
        v = ventos[i]
        c = chuvas[i]
        if c <= 0.1 and (3 <= v <= 12) and (u >= 55) and (t <= 30):
            hora_str = tempos[i].split("T")[1][:5]
            horas_boas.append(hora_str)

    if not horas_boas:
        return "Nenhuma janela 100% ideal nas próximas 24h (monitorar vento e chuva)."
    if len(horas_boas) == 1:
        return f"Por volta das *{horas_boas[0]}*"
    return f"Entre *{horas_boas[0]}* e *{horas_boas[-1]}* (nas próximas 24h)"


def consultar_clima_completo():
    url = montar_url("api." + "open-meteo.com", "v1", "forecast")
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation",
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
        "forecast_days": 2,
        "timezone": "America" + chr(47) + "Sao_Paulo"
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code != 200:
            print(f"⚠️ Erro HTTP Open-Meteo: {resp.status_code}")
            return None

        dados = resp.json()
        atual = dados.get("current", {})
        hourly = dados.get("hourly", {})

        temp = float(atual.get("temperature_2m", 0))
        umid = float(atual.get("relative_humidity_2m", 0))
        vento = float(atual.get("wind_speed_10m", 0))
        direcao = float(atual.get("wind_direction_10m", 0))
        chuva = float(atual.get("precipitation", 0))

        chuvas_prox = hourly.get("precipitation", [])[:3]
        chuva_3h = sum(chuvas_prox) if chuvas_prox else 0.0

        risco_fogo_clima = (temp > 30 and umid < 30 and vento > 30) or (umid < 25)

        return {
            "temp": temp,
            "umid": umid,
            "vento": vento,
            "direcao_texto": direcao_cardeal(direcao),
            "chuva": chuva,
            "chuva_3h": chuva_3h,
            "pulverizacao": avaliar_pulverizacao_agora(temp, umid, vento, chuva),
            "melhor_janela": encontrar_melhor_janela(hourly),
            "gado": avaliar_conforto_gado(temp, umid),
            "risco_fogo_clima": risco_fogo_clima
        }
    except Exception as e:
        print(f"Erro ao consultar clima: {e}")
        return None


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
            print(f"⚠️ Erro HTTP NASA FIRMS ({resp.status_code})")
            return []

        df = pd.read_csv(StringIO(resp.text))
        if df.empty:
            print("🛰️ Satélite NASA FIRMS consultado com sucesso: 0 focos na área.")
            return []

        focos = []
        for _, row in df.iterrows():
            dist = calcular_distancia_km(LATITUDE, LONGITUDE, float(row["latitude"]), float(row["longitude"]))
            if dist <= RAIO_KM:
                focos.append({
                    "lat": row["latitude"],
                    "lon": row["longitude"],
                    "distancia_km": round(dist, 2),
                    "confianca": row["confidence"]
                })
        focos.sort(key=lambda x: x["distancia_km"])
        return focos
    except Exception as e:
        print(f"Erro ao consultar NASA FIRMS: {e}")
        return []


def enviar_whatsapp(mensagem: str):
    if not WHATSAPP_PHONE or not WHATSAPP_APIKEY:
        print("⚠️ WhatsApp não configurado. Exibindo boletim no log:")
        print(mensagem)
        return

    url = montar_url("api." + "callmebot.com", "whatsapp.php")
    params = {
        "phone": WHATSAPP_PHONE,
        "text": mensagem,
        "apikey": WHATSAPP_APIKEY
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code == 200:
            print("✅ Boletim enviado com sucesso para o WhatsApp!")
        else:
            print(f"❌ Erro ao enviar WhatsApp ({resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"❌ Falha de conexão WhatsApp: {e}")


def executar_sentinela():
    fuso_brt = timezone(timedelta(hours=-3))
    agora_brt = datetime.now(fuso_brt)
    data_hora_str = agora_brt.strftime("%d" + chr(47) + "%m" + chr(47) + "%Y • %H:%M (Brasília)")
    evento_github = (os.getenv("GITHUB_EVENT_NAME") or "").strip()

    print(f"🚜 VectorX Agro | Varredura iniciada em {data_hora_str} (Evento: {evento_github})")

    clima = consultar_clima_completo()
    focos = verificar_focos_calor()

    # Bloco de Fogo
    if focos:
        foco_mais_proximo = focos[0]
        link_foco = montar_url("://google.com", "") + f"?q={foco_mais_proximo['lat']},{foco_mais_proximo['lon']}"
        bloco_fogo = (
            f"🔴 *ALERTA DE FOGO DETECTADO!*\n"
            f"• Foram encontrados *{len(focos)} foco(s)* no raio de {RAIO_KM:.0f} km.\n"
            f"• *Mais próximo:* a *{foco_mais_proximo['distancia_km']} km* da propriedade.\n"
            f"• *Local exato do fogo:* {link_foco}"
        )
    elif clima and clima["risco_fogo_clima"]:
        bloco_fogo = (
            "🟠 *ATENÇÃO: RISCO CLIMÁTICO DE INCÊNDIO*\n"
            "• Sem focos por satélite agora, mas o tempo seco e quente favorece queimadas."
        )
    else:
        bloco_fogo = (
            f"🟢 *Área Segura* — Nenhum foco de calor detectado por satélite num raio de {RAIO_KM:.0f} km."
        )

    # Bloco de Clima, Pulverização e Rebanho
    if clima:
        bloco_clima = (
            f"• *Temperatura:* {clima['temp']:.1f}°C\n"
            f"• *Umidade do Ar:* {clima['umid']:.0f}%\n"
    
