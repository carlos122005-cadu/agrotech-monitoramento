import os
import requests
import pandas as pd
from datetime import datetime, timezone

# =====================================================================
# CONFIGURAÇÕES (Lidas das GitHub Secrets / Variáveis de Ambiente)
# =====================================================================
# Coordenadas da propriedade (Exemplo: Centro-Oeste - ajuste para sua fazenda)
LATITUDE = float(os.getenv("FAZENDA_LAT", "-15.7801"))
LONGITUDE = float(os.getenv("FAZENDA_LON", "-47.9292"))
RAIO_KM = float(os.getenv("RAIO_ALERTA_KM", "15.0"))  # Raio de alerta de queimadas

# Chaves de API
NASA_FIRMS_KEY = os.getenv("NASA_FIRMS_KEY", "")  # Chave gratuita da NASA FIRMS
WHATSAPP_PHONE = os.getenv("WHATSAPP_PHONE", "")  # Ex: 5562999999999
WHATSAPP_APIKEY = os.getenv("WHATSAPP_APIKEY", "")  # Chave da API de WhatsApp (ex: CallMeBot)


def enviar_alerta_whatsapp(mensagem: str):
    """Envia mensagem de alerta para o WhatsApp configurado."""
    if not WHATSAPP_PHONE or not WHATSAPP_APIKEY:
        print("⚠️ Credenciais do WhatsApp não configuradas. Mensagem apenas no log:")
        print(mensagem)
        return

    url = "https://callmebot.com"
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
    """Consulta previsão/condições atuais na Open-Meteo (não exige API Key)."""
    url = (
        f"https://open-meteo.com?"
        f"latitude={LATITUDE}&longitude={LONGITUDE}"
        f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation"
        f"&timezone=America%2FSao_Paulo"
    )
    try:
        resp = requests.get(url, timeout=15).json()
        atual = resp.get("current", {})
        temp = atual.get("temperature_2m", 0)
        umid = atual.get("relative_humidity_2m", 0)
        vento = atual.get("wind_speed_10m", 0)
        chuva = atual.get("precipitation", 0)

        alertas_clima = []
        # Regra dos 30-30-30 (Risco crítico de propagação de incêndio)
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
    """Consulta focos de incêndio nas últimas 24h via satélite VIIRS (NASA FIRMS)."""
    if not NASA_FIRMS_KEY:
        print("⚠️ NASA_FIRMS_KEY não configurada. Pulando checagem de satélite.")
        return []

    # Calcula bounding box aproximado (~1 grau ≈ 111 km)
    delta = RAIO_KM / 111.0
    area = f"{LONGITUDE - delta:.4f},{LATITUDE - delta:.4f},{LONGITUDE + delta:.4f},{LATITUDE + delta:.4f}"
    
    # Fonte: VIIRS_SNPP_NRT (Alta resolução - 375m) - últimos 1 dia
    url = f"https://nasa.gov{NASA_FIRMS_KEY}/VIIRS_SNPP_NRT/{area}/1"
    
    try:
        df = pd.read_csv(url)
        if df.empty:
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
    agora = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    print(f"🛰️ Iniciando varredura VectorX Agro ({agora})...")

    alertas_clima, resumo_clima = verificar_clima()
    focos = verificar_focos_calor()

    mensagens = []

    if focos:
        mensagens.append(f"🔥 *ALERTA DE QUEIMADA DETECTADO!* ({len(focos)} foco(s) no raio de {RAIO_KM} km)")
        for i, f in enumerate(focos[:5], 1):  # Limita aos 5 primeiros para não poluir
            link_maps = f"https://google.com{f['lat']},{f['lon']}"
            mensagens.append(f"• Foco {i} (Confiança: {f['confianca']}): {link_maps}")

    if alertas_clima:
        mensagens.extend(alertas_clima)

    # Só dispara WhatsApp se houver alerta de fogo ou alerta climático crítico
    if mensagens:
        texto_final = (
            f"🚜 *VECTORX AGRO - SENTINELA 24/7*\n"
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
                                 
