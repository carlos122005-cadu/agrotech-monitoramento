import os
import math
import time
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, shape
from io import StringIO
from datetime import datetime, timezone, timedelta
from gerar_mapa import gerar_mapa_tatico

F_LAT = float((os.getenv("FAZENDA_LAT") or "-22.0286").strip().replace(",", "."))
F_LON = float((os.getenv("FAZENDA_LON") or "-42.3611").strip().replace(",", "."))
F_RAIO = float((os.getenv("RAIO_ALERTA_KM") or "15.0").strip().replace(",", "."))
_nasa = (os.getenv("NASA_FIRMS_KEY") or "").strip()
NASA_KEY = [p for p in _nasa.split("/") if len(p) == 32][-1] if "/" in _nasa else _nasa.lstrip("/")
F_PHONE = (os.getenv("WHATSAPP_PHONE") or "").strip()
SB_URL = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SB_KEY = (os.getenv("SUPABASE_KEY") or "").strip()

EVO_URL = "https://onrender.com"
EVO_HEADERS = {"apikey": "agro2026cordeiro", "Content-Type": "application/json"}


def link(dom, pedacos):
    return "https://" + ".".join(dom) + "/" + "/".join(pedacos)


def h_sb():
    return {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}


def buscar_fazendas():
    if not SB_URL or not SB_KEY:
        return []
    try:
        r = requests.post(f"{SB_URL}/rest/v1/rpc/listar_fazendas_ativas", headers=h_sb(), json={}, timeout=15)
        if r.status_code != 200:
            return []
        fazs = []
        for item in r.json():
            d = item.get("dados") or item
            nome_fz = next((d[k] for k in ["nome_fazenda", "nome", "propriedade", "fazenda", "titulo"] if d.get(k)), "Fazenda Piloto - Cordeiro/RJ")
            geom_raw = item.get("geojson") or d.get("geojson") or item.get("poligono") or d.get("geom")
            fazs.append({
                "id": d.get("id"),
                "nome": str(nome_fz).strip(),
                "lat": float(item.get("lat") or F_LAT),
                "lon": float(item.get("lon") or F_LON),
                "raio": float(d.get("raio_km") or F_RAIO),
                "tel": str(d.get("whatsapp") or d.get("telefone") or F_PHONE).strip(),
                "geom": geom_raw,
            })
        return fazs
    except Exception:
        return []


def publicar_mapa(fz, focos, iv, t_atual, u_atual):
    if not SB_URL or not SB_KEY or not focos:
        return None
    try:
        poly = shape(fz["geom"]) if isinstance(fz.get("geom"), dict) else Point(fz["lon"], fz["lat"]).buffer(0.015)
        gdf = gpd.GeoDataFrame(geometry=[poly], crs="EPSG:4326")
        focos_fmt = [{"latitude": fc["lat"], "longitude": fc["lon"]} for fc in focos[:5]]
        arq_local = f"mapa_{int(time.time())}.png"
        vel = iv["vel"] if iv else 0
        deg = (iv.get("deg", 0) + 180) % 360 if iv else 0
        gerar_mapa_tatico(gdf, fz["nome"], focos_fmt, round(t_atual, 1), round(u_atual), round(vel, 1), round(deg), arq_local)

        nome_remoto = f"alerta_{fz.get('id') or 'piloto'}_{int(time.time())}.png"
        u_up = f"{SB_URL}/storage/v1/object/mapas/{nome_remoto}"
        headers_up = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "image/png", "x-upsert": "true"}
        with open(arq_local, "rb") as f_img:
            up = requests.post(u_up, headers=headers_up, data=f_img.read(), timeout=25)
        if up.status_code in (200, 201):
            return f"{SB_URL}/storage/v1/object/public/mapas/{nome_remoto}"
    except Exception as e:
        print(f"⚠️ Falha mapa: {e}")
    return None


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
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def dir_vento(deg):
    dirs = ["Norte", "Nordeste", "Leste", "Sudeste", "Sul", "Sudoeste", "Oeste", "Noroeste"]
    return dirs[int((((deg + 180) % 360) + 22.5) // 45) % 8]


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


def enviar_wpp(tel, msg, url_img=None):
    num = "".join(filter(str.isdigit, str(tel)))
    if num and not num.startswith("55"):
        num = "55" + num
    if not num:
        print(f"⚠️ Sem telefone:\n{msg}")
        return False
    try:
        if url_img:
            ep = f"{EVO_URL}/message/sendMedia/Agrotech"
            dados = {"number": num, "mediatype": "image", "mimetype": "image/png", "caption": msg, "media": url_img, "fileName": "mapa.png", "delay": 2000}
        else:
            ep = f"{EVO_URL}/message/sendText/Agrotech"
            dados = {"number": num, "text": msg, "delay": 2000}
        r = requests.post(ep, json=dados, headers=EVO_HEADERS, timeout=60)
        ok = r.status_code in (200, 201)
        print("📲 WhatsApp enviado!" if ok else f"❌ Erro WPP ({r.status_code}): {r.text[:120]}")
        if ok:
            time.sleep(5)
        return ok
    except Exception as e:
        print(f"❌ Falha WPP: {e}")
        return False


def ver_clima(lat, lon, agora):
    u = f"{link(['api', 'open-meteo', 'com'], ['v1', 'forecast'])}?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation,precipitation_probability&timezone=America%2FSao_Paulo&forecast_days=2"
    dados = None
    for _ in range(2):
        try:
            r = requests.get(u, headers={"User-Agent": "VectorXAgro/1.0"}, timeout=20)
            if r.status_code == 200 and r.text.strip():
                dados = r.json()
                break
        except Exception:
            time.sleep(2)
    if not dados:
        return [], "Dados climáticos indisponíveis no momento.", None

    hr, dd = dados.get("current", {}), dados.get("hourly", {})
    t, u_ar, v = float(hr.get("temperature_2m", 0)), float(hr.get("relative_humidity_2m", 0)), float(hr.get("wind_speed_10m", 0))
    dv, ch = float(hr.get("wind_direction_10m", 0)), float(hr.get("precipitation", 0))
    hs = agora.strftime("%Y-%m-%dT%H:00")
    th = dd.get("time", [])
    idx = th.index(hs) if hs in th else 0
    ph = dd.get("precipitation_probability", [])
    pa = float(ph[idx]) if 0 <= idx < len(ph) else 0.0

    alertas = []
    if t >= 30 and u_ar <= 30 and v >= 30:
        alertas.append("🔥 *RISCO EXTREMO DE INCÊNDIO (Regra 30-30-30 atingida!)*")
    if u_ar < 25:
        alertas.append("⚠️ *Umidade relativa crítica (< 25%)*")
    if v > 40:
        alertas.append(f"💨 *Rajadas de vento fortes ({v:.0f}km/h)*")

    st_p, _ = eval_pulv(t, u_ar, v, ch, pa)
    mej_ph = "Sem janela ideal nas próximas 24h"
    for i in range(idx + 1, min(idx + 25, len(th))):
        ht, hu, hv = float(dd["temperature_2m"][i]), float(dd["relative_humidity_2m"][i]), float(dd["wind_speed_10m"][i])
        hc, hp = float(dd["precipitation"][i] or 0), float(dd["precipitation_probability"][i] or 0)
        if eval_pulv(ht, hu, hv, hc, hp)[1] >= 80:
            dia_lbl = "Hoje" if th[i].startswith(agora.strftime("%Y-%m-%d")) else "Amanhã"
            mej_ph = f"{dia_lbl} às {th[i][-5:]} ({ht:.0f}°C | Vento {hv:.0f}km/h | Umid {hu:.0f}%)"
            break

    destino = dir_vento(dv)
    res = f"🌡️ Temp: {t:.1f}°C | 💧 Umid: {u_ar:.0f}%\n💨 Vento: {v:.1f}km/h (indo p/ {destino})\n🌧️ Chuva: {ch:.1f}mm (Chance: {pa:.0f}%)\n🚜 *Pulverização:* {st_p}\n⏰ *Melhor janela:* {mej_ph}\n{eval_gado(t, u_ar)}"
    return alertas, res, {"vel": v, "dest": destino, "deg": dv, "t": t, "u": u_ar}


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
