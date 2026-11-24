import os
import requests
import numpy as np
import rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds
from rasterio.enums import Resampling
from datetime import datetime, timezone, timedelta

SB_URL = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SB_KEY = (os.getenv("SUPABASE_KEY") or "").strip()
F_LAT = float((os.getenv("FAZENDA_LAT") or "-22.0286").strip().replace(",", "."))
F_LON = float((os.getenv("FAZENDA_LON") or "-42.3611").strip().replace(",", "."))
F_PHONE = (os.getenv("WHATSAPP_PHONE") or "").strip()
F_APIKEY = (os.getenv("WHATSAPP_APIKEY") or "").strip()


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
            return []
        fazs = []
        for item in r.json():
            d = item.get("dados") or item
            nome_fz = next((d[k] for k in ["fazenda_nome", "nome_fazenda", "nome", "propriedade"] if d.get(k)), "Fazenda Piloto - Cordeiro/RJ")
            fazs.append({
                "nome": str(nome_fz).strip(),
                "lat": float(item.get("lat") or F_LAT),
                "lon": float(item.get("lon") or F_LON),
                "tel": str(d.get("whatsapp") or F_PHONE).strip(),
                "chave": str(d.get("apikey_whatsapp") or F_APIKEY).strip(),
            })
        return fazs
    except Exception:
        return []


def ler_cena_limpa(cena, bbox):
    assets = cena.get("assets", {})
    url_red = assets.get("red", {}).get("href")
    url_nir = assets.get("nir", {}).get("href")
    url_scl = assets.get("scl", {}).get("href")
    if not url_red or not url_nir:
        return None
    with rasterio.open(url_red) as src_r:
        b_proj = transform_bounds("EPSG:4326", src_r.crs, *bbox)
        win = from_bounds(*b_proj, src_r.transform)
        red = src_r.read(1, window=win).astype("float32")
    with rasterio.open(url_nir) as src_n:
        nir = src_n.read(1, window=win).astype("float32")

    valido = (red > 0) & (nir > 0)
    if url_scl:
        with rasterio.open(url_scl) as src_s:
            win_s = from_bounds(*b_proj, src_s.transform)
            scl = src_s.read(1, window=win_s, out_shape=red.shape, resampling=Resampling.nearest)
            valido = valido & np.isin(scl, [4, 5])

    if np.mean(valido) < 0.30:
        return None
    soma = nir + red
    ndvi = np.where(valido & (soma > 0), (nir - red) / soma, np.nan)
    return round(float(np.nanmean(ndvi)), 2)


def calcular_ndvi_sentinel(lat, lon):
    stac_url = link(["earth-search", "aws", "element84", "com"], ["v1", "search"])
    fim = datetime.now(timezone.utc)
    ini = fim - timedelta(days=75)
    bbox = [lon - 0.008, lat - 0.008, lon + 0.008, lat + 0.008]
    payload = {
        "collections": ["sentinel-2-l2a"],
        "bbox": bbox,
        "datetime": f"{ini.strftime('%Y-%m-%dT00:00:00Z')}/{fim.strftime('%Y-%m-%dT23:59:59Z')}",
        "query": {"eo:cloud_cover": {"lt": 85}},
        "limit": 12
    }
    try:
        r = requests.post(stac_url, json=payload, timeout=25)
        feats = r.json().get("features", [])
        leituras = []
        for cena in feats:
            val = ler_cena_limpa(cena, bbox)
            if val is not None:
                dt_raw = cena["properties"].get("datetime", "")[:10]
                dt_br = "/".join(reversed(dt_raw.split("-"))) if "-" in dt_raw else dt_raw
                leituras.append((val, dt_br))
                if len(leituras) == 2:
                    break
        if not leituras:
            return None, None, "Céu encoberto sobre a fazenda nas últimas semanas (aguardando abertura)."
        atual_val, atual_dt = next(iter(leituras))
        ant_val = list(leituras).pop(1)[0] if len(leituras) >= 2 else None
        return atual_val, ant_val, atual_dt
    except Exception as e:
        return None, None, f"Falha satélite: {str(e)[:50]}"


def montar_diagnostico(ndvi, ant):
    if ndvi is None:
        return "☁️ *Situação:* Nuvens exatamente sobre a propriedade impediram a leitura limpa."
    nota = max(0.0, min(10.0, round(ndvi * 10, 1)))
    tend = ""
    if ant is not None:
        diff = round((ndvi - ant) * 10, 1)
        if diff >= 0.3:
            tend = f"📈 *Tendência:* Melhorando (+{diff} pts desde a última foto)\n"
        elif diff <= -0.3:
            tend = f"📉 *Tendência:* Caindo ({diff} pts desde a última foto)\n"
        else:
            tend = "➡️ *Tendência:* Estável em relação à última leitura\n"

    if ndvi >= 0.55:
        st = "🟢 *Pasto Verde e Cheio (Excelente)*"
        acao = "Boa oferta de capim para o rebanho. Mantenha o rodízio normal dos piquetes."
    elif ndvi >= 0.38:
        st = "🟡 *Pasto Médio (Atenção)*"
        acao = "Capim com vigor moderado. Observe a lotação e planeje descanso ou adubação."
    else:
        st = "🔴 *Pasto Seco / Ralo (Alerta)*"
        acao = "Pouca folha verde disponível. Recomendado aliviar o pasto, dar descanso ao piquete ou reforçar o cocho."

    return f"🌱 *Nota do Pasto:* *{nota:.1f} / 10* (Índice {ndvi:.2f})\n{tend}{st}\n💡 *Na prática:* {acao}"


def enviar_wpp(tel, chave, msg):
    if not tel or not chave:
        print(msg)
        return
    requests.get(link(["api", "callmebot", "com"], ["whatsapp.php"]), params={"phone": tel, "text": msg, "apikey": chave}, timeout=25)


def principal():
    agora = datetime.now(timezone(timedelta(hours=-3)))
    faz = buscar_fazendas() or [{"nome": "Fazenda Piloto - Cordeiro/RJ", "lat": F_LAT, "lon": F_LON, "tel": F_PHONE, "chave": F_APIKEY}]
    for fz in faz:
        ndvi, ant, info = calcular_ndvi_sentinel(fz["lat"], fz["lon"])
        corpo = montar_diagnostico(ndvi, ant)
        cab_foto = f"📸 *Última foto sem nuvem:* {info}\n\n" if ndvi is not None else f"{info}\n\n"
        msg = (
            f"🛰️ *RAIO-X DO PASTO — {fz['nome'].upper()}*\n"
            f"Relatório Semanal • {agora.strftime('%d/%m/%Y')}\n\n"
            f"{cab_foto}{corpo}\n\n"
            f"_Satélite Sentinel-2 • VectorX Agro_"
        )
        enviar_wpp(fz["tel"], fz["chave"], msg)


if __name__ == "__main__":
    principal()
# FIM_NDVI
