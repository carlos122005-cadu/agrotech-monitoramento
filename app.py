import os
import json
import xml.etree.ElementTree as ET
import streamlit as st
import requests
from shapely.geometry import Polygon

st.set_page_config(page_title="VectorX Agro — Painel", page_icon="🌾", layout="centered")
st.title("🌾 VectorX Agro — Cadastro de Propriedades")
st.caption("Inteligência Territorial e Monitoramento Rural • Cordeiro/RJ")

SB_URL = (os.getenv("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")).strip().rstrip("/")
SB_KEY = (os.getenv("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY", "")).strip()
SENHA_ADMIN = (os.getenv("ADMIN_PASSWORD") or st.secrets.get("ADMIN_PASSWORD", "vectorx2026")).strip()


def ler_kml(conteudo_bytes):
    raiz = ET.fromstring(conteudo_bytes)
    coords = []
    for elem in raiz.iter():
        if elem.tag.endswith("coordinates") and elem.text:
            for par in elem.text.strip().split():
                p = par.split(",")
                if len(p) >= 2:
                    it = iter(p)
                    coords.append((float(next(it)), float(next(it))))
            if len(coords) >= 3:
                break
    return Polygon(coords) if len(coords) >= 3 else None


def extrair_poligono(arquivo):
    nome = arquivo.name.lower()
    raw = arquivo.read()
    if nome.endswith(".kml"):
        return ler_kml(raw)
    data = json.loads(raw.decode("utf-8"))
    if data.get("type") == "FeatureCollection":
        geom = next(iter(data["features"]))["geometry"]
    elif data.get("type") == "Feature":
        geom = data["geometry"]
    else:
        geom = data
    anel = next(iter(geom["coordinates"]))
    if geom["type"] == "MultiPolygon":
        anel = next(iter(anel))
    return Polygon([(next(iter(c)), list(c).pop(1)) for c in anel])


senha = st.text_input("🔒 Senha de Administrador", type="password")
if senha != SENHA_ADMIN:
    st.info("Digite a senha administrativa para cadastrar propriedades.")
    st.stop()

with st.form("cadastro_fazenda"):
    st.subheader("📍 Nova Fazenda Monitorada")
    fazenda_nome = st.text_input("Nome da Propriedade", placeholder="Ex: Sítio Boa Esperança - Cordeiro/RJ")
    whatsapp = st.text_input("WhatsApp do Produtor (com DDI 55 + DDD)", placeholder="Ex: 5522999999999")
    apikey_wpp = st.text_input("API Key CallMeBot", placeholder="Ex: 1234567")
    cultura = st.text_input("Cultura / Atividade Principal", value="Pastagem e Silagem")
    raio_km = st.number_input("Raio de Alerta de Queimadas (km)", min_value=2.0, max_value=50.0, value=15.0, step=1.0)
    arq_geo = st.file_uploader("Arquivo do Perímetro (.KML ou .GeoJSON / CAR)", type=["kml", "geojson", "json"])
    usar_exemplo = st.checkbox("🧪 Usar polígono de exemplo em Cordeiro/RJ (teste rápido sem arquivo)")
    enviado = st.form_submit_button("✅ Cadastrar Fazenda no Sistema")

if enviado:
    if not fazenda_nome or not whatsapp or (not arq_geo and not usar_exemplo):
        st.error("Preencha o nome, o WhatsApp e envie um arquivo (ou marque o polígono de exemplo).")
    else:
        try:
            if usar_exemplo and not arq_geo:
                poly = Polygon([(-42.365, -22.025), (-42.355, -22.025), (-42.355, -22.032), (-42.365, -22.032), (-42.365, -22.025)])
            else:
                poly = extrair_poligono(arq_geo)
            if not poly or not poly.is_valid:
                st.error("Não foi possível ler um polígono válido.")
            else:
                centro = poly.centroid
                headers = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}
                payload = {
                    "fazenda_nome": fazenda_nome.strip(),
                    "whatsapp": whatsapp.strip(),
                    "apikey_whatsapp": apikey_wpp.strip(),
                    "cultura": cultura.strip(),
                    "raio_km": float(raio_km),
                    "geometria": f"SRID=4326;{poly.wkt}",
                    "ativo": True
                }
                r = requests.post(f"{SB_URL}/rest/v1/fazendas", headers=headers, json=payload, timeout=15)
                if r.status_code in (200, 201):
                    st.success(f"🎉 **{fazenda_nome}** cadastrada com sucesso! Centroide: ({centro.y:.4f}, {centro.x:.4f})")
                else:
                    st.error(f"Erro ao salvar no Supabase ({r.status_code}): {r.text}")
        except Exception as e:
            st.error(f"Falha ao processar: {e}")
# FIM_APP
