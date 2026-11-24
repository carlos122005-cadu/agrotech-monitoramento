import os
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
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            print("✅ Alerta enviado com sucesso no WhatsApp!")
        else:
            print(f"❌ Erro ao enviar WhatsApp: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"❌ Falha de conexão com API do WhatsApp: {e}")


def verificar_clima():
    base_clima = criar_link(["api", "open-meteo", "com"], "v1", "forecast")
    url = (
        f"{base_clima}"
        f"?latitude={LATITUDE}&longitude={LONGITUDE}"
        f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation"
        f"&timezone=America/Sao_Paulo"
    )
    try:
        resp = requests.get(url, headers={"User-Agent": "VectorXAgro/1.0"}, timeout=20)
        if resp.status_code != 200 or not resp.text.strip():
            print(f"⚠️ Resposta inválida Open-Meteo (Status {resp.status_code}): {resp.text[:150]}")
            return [], "Dados climáticos indisponíveis no momento."

        dados = resp.json()
        atual = dados.get("current", {})
        temp = float(atual.get("temperature_2m", 0))
        umid = float(atual.get("relative_humidity_2m", 0))
        vento = float(atual.get("wind_speed_10m", 0))
        chuva = float(atual.get("precipitation", 0))

        alertas_clima = []
        if temp >= 30 and umid <= 30 and vento >= 30:
            alertas_clima.append("🚨 *RISCO EXTREMO DE INCÊNDIO (Regra 30-30-30 atingida!)*")
        elif umid < 25:
            alertas_clima.append("⚠️ *Umidade relativa crítica (< 25%)*")

        if vento > 40:
            alertas_clima.append(f"💨 *Rajadas de vento fortes ({vento} km/h)*")

        # Pulverização e Adubação
        if chuva > 0.2 or vento > 15 or umid < 45 or temp > 32:
            pulv = "🔴 *Pulverização/Adubação:* Não aplicar agora"
        elif vento >= 10 or umid < 55 or temp > 28:
            pulv = "🟡 *Pulverização/Adubação:* Aplicar com cautela"
        else:
            pulv = "🟢 *Pulverização/Adubação:* Janela ideal agora"

        # Conforto do Rebanho (sem siglas técnicas)
        itu = 0.8 * temp + (umid / 100.0) * (temp - 14.4) + 46.4
        if itu >= 84:
            gado = "🚨 *Rebanho:* Calor crítico! Sombra, água e zero manejo."
        elif itu >= 78:
            gado = "🟠 *Rebanho:* Desconforto térmico. Manter na sombra e evitar curral."
        elif itu >= 72:
            gado = "🟡 *Rebanho:* Atenção leve. Preferir manejo no início da manhã."
        else:
            gado = "🟢 *Rebanho:* Conforto térmico ideal."

        resumo = (
            f"🌡️ Temp: {temp}°C | 💧 Umid: {umid}%\n"
            f"💨 Vento: {vento} km/h | 🌧️ Chuva: {chuva} mm\n\n"
            f"{pulv}\n{gado}"
        )
        return alertas_clima, resumo
    except Exception as e:
        print(f"Erro ao consultar clima: {e}")
        return [], "Dados climáticos indisponíveis no momento."


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
    fuso_brt = timezone(timedelta(hours=-3))
    agora_brt = datetime.now(fuso_brt)
    agora_str = agora_brt.strftime("%d/%m/%Y %H:%M (Brasília)")
    evento = (os.getenv("GITHUB_EVENT_NAME") or "").strip()

    print(f"🛰️ Iniciando varredura VectorX Agro ({agora_str}) | Evento: {evento}...")

    alertas_clima, resumo_clima = verificar_clima()
    focos = verificar_focos_calor()

    mensagens = []

    if focos:
        mensagens.append(f"🔥 *ALERTA DE QUEIMADA DETECTADO!* ({len(focos)} foco(s) no raio de {RAIO_KM} km)")
        base_mapa = criar_link(["www", "google", "com"], "maps")
        for i, f in enumerate(focos[:5], 1):
            link_maps = f"{base_mapa}?q={f['lat']},{f['lon']}"
            mensagens.append(f"• Foco {i} (Confiança: {f['confianca']}): {link_maps}")

    if alertas_clima:
        mensagens.extend(alertas_clima)

    if mensagens or agora_brt.hour == 6 or evento == "workflow_dispatch":
        cabecalho_status = "\n".join(mensagens) if mensagens else f"🛡️ *Área Segura:* Nenhum foco de calor no raio de {RAIO_KM} km."
        texto_final = (
            f"🚜 *VECTORX AGRO - SENTINELA 24/7*\n"
            f"🕒 {agora_str}\n\n"
            f"{cabecalho_status}\n\n"
            f"📊 *Condições Atuais & Manejo:*\n{resumo_clima}"
        )
        enviar_alerta_whatsapp(texto_final)
    else:
        print("✅ Nenhum foco de calor ou risco climático crítico detectado (Zero Spam).")
        print(resumo_clima)


if __name__ == "__main__":
    executar_sentinela()
            
