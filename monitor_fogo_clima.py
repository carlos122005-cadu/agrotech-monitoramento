import os
import math
import time
import requests
import pandas as pd
from io import StringIO
from datetime import datetime, timezone, timedelta

F_LAT = float((os.getenv("FAZENDA_LAT") or "-22.0286").strip().replace(",", "."))
F_LON = float((os.getenv("FAZENDA_LON") or "-42.3611").strip().replace(",", "."))
F_RAIO = float((os.getenv("RAIO_ALERTA_KM") or "15.0").strip().replace(",", "."))
_nasa = (os.getenv("NASA_FIRMS_KEY") or "").strip()
NASA_KEY = [p for p in _nasa.split("/") if len(p) == 32][-1] if "/" in _nasa else _nasa.lstrip("/")
F_PHONE = (os.getenv("WHATSAPP_PHONE") or "").strip()
F_APIKEY = (os.getenv("WHATSAPP_APIKEY") or "").strip()
SB_URL = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SB_KEY = (os.getenv("SUPABASE_KEY") or "").strip()


def link(dom, pedacos):
    b = "/"
    return "https:" + b + b + ".".join(dom) + b + b.join(pedacos)


def h_sb():
    return {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}


def buscar_fazendas():
    if not SB_URL or not SB_KEY:
        return []
    try:
        r = requests.post(f"{SB_URL}/rest/v1/rpc/listar_fazendas_ativas", headers=h_sb(), json={}, timeout=15)
        if r.status_code != 200:
            print(f"⚠️ Erro Supabase ({r.status_code}): {r.text[:120]}")
            return []
        fazs = []
        for item in r.json():
            d = item.get("dados") or {}
            fazs.append({
                "id": d.get("id"),
                "nome": d.get("nome_fazenda") or d.get("nome") or d.get("propriedade") or "Fazenda VectorX",
                "lat": float(item.get("lat") or F_LAT),
                "lon": float(item.get("lon") or F_LON),
                "raio": float(d.get("raio_km") or F_RAIO),
                "tel": str(d.get("whatsapp") or d.get("telefone") or F_PHONE).strip(),
                "chave": str(d.get("apikey_whatsapp") or d.get("chave_de_API") or F_APIKEY).strip(),
            })
        return fazs
    except Exception as e:
        print(f"⚠️ Falha Supabase: {e}")
        return []


def ja_alertado(fid, tipo):
    if not SB_URL or not SB_KEY or not fid:
        return False
    lim = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    p = {"select": "id", "fazenda_id": f"eq.{fid}", "tipo_alerta": f"eq.{tipo}", "created_at": f"gte.{lim}"}
    try:
        r = requests.get(f"{SB_URL}/rest/v1/alertas_enviados", headers=h_sb(), params=p, timeout=15)
        return r.status_code == 200 and len(r.json()) > 0
    except Exception:
        return False


def salvar_alerta(fid, tipo):
    if not SB_URL or not SB_KEY or not fid:
        return
    u = f"{SB_URL}/rest/v1/alertas_enviados"
    try:
        requests.post(u, headers=h_sb(), json={"fazenda_id": fid, "tipo_alerta": tipo}, timeout=15)
        lim48 = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        requests.delete(u, headers=h_sb(), params={"created_at": f"lt.{lim48}"}, timeout=15)
    except Exception:
        pass


def dist_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def dir_vento(deg):
    prop = (deg + 180) % 360
    dirs = ["Norte", "Nordeste", "Leste", "Sudeste", "Sul", "Sudoeste", "Oeste", "Noroeste"]
    return dirs[int((prop + 22.5) // 45) % 8]


def eval_pulv(t, u, v, c, p):
    if c > 0.2 or p >= 55 or v > 15 or u < 45 or t > 32:
        return "🔴 NÃO APLICAR (Risco de chuva, deriva ou evaporação)", 10
    if p >= 30 or u >= 90 or v >= 10 or u <= 55 or t >= 28 or v < 3:
        return "🟡 CAUTELA (Orvalho/umidade alta, vento ou chance de pancada)", 80
    return "🟢 IDEAL (Janela ótima para brilho e adubação)", 100


def eval_gado(t, u):
    itu = 0.8 * t + (u / 100.0) * (t - 14.4) + 46.4
    if itu >= 84:
        return "🔴 *Rebanho:* Calor crítico! Sombra imediata, água fresca e zero manejo."
    if itu >= 78:
        return "🟠 *Rebanho:* Desconforto térmico. Manter na sombra e evitar curral nas horas quentes."
    if itu >= 72:
        return "🟡 *Rebanho:* Atenção leve. Prefira manejo no início da manhã."
    return "🟢 *Rebanho:* Conforto térmico ideal."


def enviar_wpp(tel, chave, msg):
    if not tel or not chave:
        print(f"⚠️ Sem credenciais WPP:\n{msg}")
        return False
    try:
        r = requests.get(
            link(["api", "callmebot", "com"], ["whatsapp.php"]),
            params={"phone": tel, "text": msg, "apikey": chave},
            timeout=25
        )
        print(f"📲 WhatsApp enviado!" if r.status_code == 200 else f"❌ Erro WPP: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Falha WPP: {e}")
        return False


def ver_clima(lat, lon, agora):
    u = f"{link(['api', 'open-meteo', 'com'], ['v1', 'forecast'])}?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation,precipitation_probability&timezone=America%2FSao_Paulo&forecast_days=2"
    dados = None
    for _ in range(2):
        try:
            r = requests.get(u, headers={"User-Agent": "VectorXAgro/1.0"}, timeout=25)
            if r.status_code == 200 and r.text.strip():
                dados = r.json()
                break
        except Exception:
            time.sleep(2)
    if not dados:
        return [], "Dados climáticos indisponíveis no momento.", None

    em, hr = dados.get("current", {}), dados.get("hourly", {})
    t, u_ar, v = float(em.get("temperature_2m", 0)), float(em.get("relative_humidity_2m", 0)), float(em.get("wind_speed_10m", 0))
    dv, ch = float(em.get("wind_direction_10m", 0)), float(em.get("precipitation", 0))
    hs = agora.strftime("%Y-%m-%dT%H:00")
    th = hr.get("time", [])
    idx = th.index(hs) if hs in th else 0
    ph = hr.get("precipitation_probability", [])
    pa = float(ph[idx]) if 0 <= idx < len(ph) else 0.0

    alertas = []
    if t >= 30 and u_ar <= 30 and v >= 30:
        alertas.append("🔥 *RISCO EXTREMO DE INCÊNDIO (Regra 30-30-30 atingida!)*")
    elif u_ar < 25:
        alertas.append("⚠️ *Umidade relativa crítica (< 25%)*")
    if v > 40:
        alertas.append(f"💨 *Rajadas de vento fortes ({v:.0f}km/h)*")

    st_p, _ = eval_pulv(t, u_ar, v, ch, pa)
    ms_ph = "Sem janela ideal nas próximas 24h"
    for i in range(idx + 1, min(idx + 25, len(th))):
        ht, hu, hv = float(hr["temperature_2m"][i]), float(hr["relative_humidity_2m"][i]), float(hr["wind_speed_10m"][i])
        hc, hp = float(hr["precipitation"][i] or 0), float(hr["precipitation_probability"][i] or 0)
        _, sc = eval_pulv(ht, hu, hv, hc, hp)
        if sc >= 80:
            ms_ph = f"{th[i].replace('T', ' ')} ({ht:.0f}°C | Vento:{hv:.0f}km/h | Umid:{hu:.0f}%)"
            break

    destino = dir_vento(dv)
    res = (
        f"🌡️ Temp: {t:.1f}°C | 💧 Umid: {u_ar:.0f}%\n"
        f"💨 Vento: {v:.1f}km/h (indo p/ {destino})\n"
        f"🌧️ Chuva: {ch:.1f}mm (Chance: {pa:.0f}%)\n"
        f"🚜 *Pulverização/Adubo:* {st_p}\n"
        f"🕒 *Próx. janela boa:* {ms_ph}\n"
        f"{eval_gado(t, u_ar)}"
    )
    return alertas, res, {"vel": v, "dest": destino}


def baixar_focos(faz):
    if not NASA_KEY or not faz:
        return pd.DataFrame()
    lats, lons = [f["lat"] for f in faz], [f["lon"] for f in faz]
    area = f"{min(lons)-1:.4f},{min(lats)-1:.4f},{max(lons)+1:.4f},{max(lats)+1:.4f}"
    u = link(["firms", "modaps", "eosdis", "nasa", "gov"], ["api", "area", "csv", NASA_KEY, "VIIRS_SNPP_NRT", area, "1"])
    try:
        r = requests.get(u, headers={"User-Agent": "VectorXAgro/1.0"}, timeout=25)
        return pd.read_csv(StringIO(r.text)) if r.status_code == 200 else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def principal():
    agora = datetime.now(timezone(timedelta(hours=-3)))
    astr = agora.strftime("%d/%m/%Y %H:%M (Brasília)")
    ev = (os.getenv("GITHUB_EVENT_NAME") or "").strip()
    print(f"🚀 Varredura VectorX Agro ({astr}) | Evento: {ev}")

    faz = buscar_fazendas()
    print(f"🏡 {len(faz)} fazenda(s) carregada(s) do Supabase." if faz else "🏡 Usando segredos alternativos.")
    if not faz:
        faz = [{"id": None, "nome": "Fazenda Piloto - Cordeiro/RJ", "lat": F_LAT, "lon": F_LON, "raio": F_RAIO, "tel": F_PHONE, "chave": F_APIKEY}]

    df = baixar_focos(faz)
    for fz in faz:
        ac, rc, iv = ver_clima(fz["lat"], fz["lon"], agora)
        focos = []
        if not df.empty:
            for _, linha in df.iterrows():
                if str(linha.get("confidence", "")).lower() != "l" and float(linha.get("frp", 0)) >= 2.0:
                    d = dist_km(fz["lat"], fz["lon"], float(linha["latitude"]), float(linha["longitude"]))
                    if d <= fz["raio"]:
                        focos.append({"lat": float(linha["latitude"]), "lon": float(linha["longitude"]), "d": round(d, 2)})
        focos.sort(key=lambda x: x["d"])
        mensagens, chave = [], None

        if focos:
            mp = focos[0]
            chave = f"FOGO_{round(mp['lat'], 2)}_{round(mp['lon'], 2)}"
            mensagens.append(f"🔥 *ALERTA DE QUEIMADA DETECTADO!*\n• Mais próximo: a *{mp['d']}km* da propriedade.")
            if iv:
                mensagens.append(f"• Propagação do Vento: *{iv['vel']:.1f}km/h* afundando para *{iv['dest']}*")
            lm = link(["www", "google", "com"], ["maps"])
            for i, fc in enumerate(focos[:3], 1):
                mensagens.append(f"• Mapa Foco {i} ({fc['d']}km): {lm}?q={fc['lat']},{fc['lon']}")
        if ac:
            mensagens.extend(ac)
            chave = chave or f"CLIMA_{agora.strftime('%Y%m%d')}"

        if ev == "workflow_dispatch" or (agora.hour == 6 and agora.minute < 30):
    
