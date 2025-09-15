# -*- coding: utf-8 -*-
import os
import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
import re
import uuid
import hashlib
from datetime import date, datetime, timedelta
from calendar import monthrange, Calendar
from urllib.parse import quote
from io import StringIO, BytesIO

# ============================== CONFIG ==============================
st.set_page_config(page_title="✨ Villa Tobias — Réservations", page_icon="✨", layout="wide")

CSV_RESERVATIONS = "reservations.csv"
CSV_PLATEFORMES = "plateformes.csv"

DEFAULT_PALETTE = {
    "Booking": "#1e90ff",
    "Airbnb": "#e74c3c",
    "Abritel": "#8e44ad",
    "Autre": "#f59e0b",
}

# Liens Google
FORM_SHORT_URL = "https://urlr.me/kZuH94"
GOOGLE_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLScLiaqSAY3JYriYZIk9qP75YGUyP0sxF8pzmhbIQqsSEY0jpQ/viewform"
GOOGLE_SHEET_EMBED_URL = "https://docs.google.com/spreadsheets/d/1ci-4i8dZWzixt0p5WPdB2D8ePCpNQDD0jjZf41KtYns/edit?usp=sharing"
GOOGLE_SHEET_PUBLISHED_CSV = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSMie1mawlXGJtqC7KL_gSgeC9e8jwOxcqMzC1HmxxU8FCrOxD0HXl5APTO939__tu7EPh6aiXHnSnF/pub?output=csv"

# ============================== STYLE ==============================
def apply_style(light: bool):
    bg = "#fafafa" if light else "#0f1115"
    fg = "#0f172a" if light else "#eaeef6"
    side = "#f2f2f2" if light else "#171923"
    border = "rgba(17,24,39,.08)" if light else "rgba(124,92,255,.16)"
    chip_bg = "#333" if not light else "#e8e8e8"
    chip_fg = "#eee" if not light else "#222"
    st.markdown(
        f"""
        <style>
          html, body, [data-testid="stAppViewContainer"] {{
            background: {bg}; color: {fg};
          }}
          [data-testid="stSidebar"] {{
            background: {side}; border-right: 1px solid {border};
          }}
          .glass {{
            background: {"rgba(255,255,255,0.65)" if light else "rgba(255,255,255,0.06)"};
            border: 1px solid {border}; border-radius: 12px; padding: 12px; margin: 8px 0;
          }}
          .chip {{
            display:inline-block; background:{chip_bg}; color:{chip_fg};
            padding:6px 10px; border-radius:12px; margin:4px 6px; font-size:0.86rem
          }}
          .kpi-line strong {{ font-size:1.05rem; }}
          .cal-grid {{ display:grid; grid-template-columns: repeat(7, 1fr); gap:8px; margin-top:8px; }}
          .cal-cell {{
            border:1px solid {border}; border-radius:10px; min-height:110px; padding:8px;
            position:relative; overflow:hidden; background:{"#fff" if light else "#0b0d12"};
          }}
          .cal-cell.outside {{ opacity:.45; }}
          .cal-date {{ position:absolute; top:6px; right:8px; font-weight:700; font-size:.9rem; opacity:.7; }}
          .resa-pill {{
            padding:4px 6px; border-radius:6px; font-size:.85rem; margin-top:22px;
            color:#fff; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
          }}
          .cal-header {{
            display:grid; grid-template-columns: repeat(7, 1fr);
            font-weight:700; opacity:.8; margin-top:10px;
          }}
        </style>
        """,
        unsafe_allow_html=True
    )

# ============================== DATA HELPERS ==============================
BASE_COLS = [
    "paye", "nom_client", "email", "sms_envoye", "post_depart_envoye",
    "plateforme", "telephone",
    "date_arrivee", "date_depart", "nuitees",
    "prix_brut", "commissions", "frais_cb", "prix_net", "menage", "taxes_sejour", "base", "charges", "%",
    "res_id", "ical_uid"
]

def _as_series(x, index=None):
    if isinstance(x, pd.Series):
        return x
    if isinstance(x, (list, tuple, np.ndarray)):
        s = pd.Series(list(x))
        if index is not None and len(index) == len(s):
            s.index = index
        return s
    if index is None:
        return pd.Series([x])
    return pd.Series([x] * len(index), index=index)

def _detect_delimiter_and_read(raw: bytes) -> pd.DataFrame:
    if raw is None:
        return pd.DataFrame()
    txt = raw.decode("utf-8", errors="ignore").replace("\ufeff", "")
    for sep in [";", ",", "\t", "|"]:
        try:
            df = pd.read_csv(StringIO(txt), sep=sep, dtype=str)
            if df.shape[1] >= 2:
                return df
        except Exception:
            pass
    try:
        return pd.read_csv(StringIO(txt), dtype=str)
    except Exception:
        return pd.DataFrame()

def _to_bool_series(s: pd.Series) -> pd.Series:
    s = _as_series(s)
    out = s.astype(str).str.strip().str.lower().isin(["true", "1", "oui", "vrai", "yes", "y", "t"])
    return out.fillna(False).astype(bool)

def _to_num(s: pd.Series) -> pd.Series:
    s = _as_series(s)
    sc = (
        s.astype(str)
        .str.replace("€", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    return pd.to_numeric(sc, errors="coerce")

def _to_date(s: pd.Series) -> pd.Series:
    s = _as_series(s)
    d = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if len(d) and d.isna().mean() > 0.5:
        d2 = pd.to_datetime(s, errors="coerce", format="%Y-%m-%d")
        d = d.fillna(d2)
    return d.dt.date

def build_stable_uid(row) -> str:
    base = f"{row.get('res_id', '')}{row.get('nom_client', '')}{row.get('telephone', '')}"
    return hashlib.sha1(base.encode()).hexdigest() + "@villa-tobias"

@st.cache_data
def _load_file_bytes(path: str):
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception as e:
        st.warning(f"Fichier {path} introuvable ou illisible. Un fichier vide sera créé.")
        return None

@st.cache_data
def charger_donnees():
    # Créer les fichiers s'ils n'existent pas
    for fichier, header in [
        (CSV_RESERVATIONS, "nom_client,email,telephone,plateforme,date_arrivee,date_depart,nuitees,prix_brut\n"),
        (CSV_PLATEFORMES, "plateforme,couleur\nBooking,#1e90ff\nAirbnb,#e74c3c\n")
    ]:
        if not os.path.exists(fichier):
            with open(fichier, "w") as f:
                f.write(header)

    # Charger les réservations
    raw = _load_file_bytes(CSV_RESERVATIONS)
    base_df = _detect_delimiter_and_read(raw) if raw is not None else pd.DataFrame()
    df = ensure_schema(base_df)

    # Charger la palette
    rawp = _load_file_bytes(CSV_PLATEFORMES)
    palette = DEFAULT_PALETTE.copy()
    if rawp is not None:
        try:
            pal_df = _detect_delimiter_and_read(rawp)
            pal_df.columns = pal_df.columns.astype(str).str.strip()
            if {"plateforme", "couleur"}.issubset(pal_df.columns):
                palette = dict(zip(pal_df["plateforme"], pal_df["couleur"]))
        except Exception as e:
            st.warning(f"Erreur de chargement de la palette : {e}")

    return df, palette

def ensure_schema(df_in: pd.DataFrame) -> pd.DataFrame:
    if df_in is None or len(df_in) == 0:
        return pd.DataFrame(columns=BASE_COLS)

    df = df_in.copy()
    df.columns = df.columns.astype(str).str.strip()

    rename_map = {
        'Payé': 'paye', 'Client': 'nom_client', 'Plateforme': 'plateforme',
        'Arrivée': 'date_arrivee', 'Départ': 'date_depart', 'Nuits': 'nuitees',
        'Brut (€)': 'prix_brut'
    }
    df.rename(columns=rename_map, inplace=True)

    for c in BASE_COLS:
        if c not in df.columns:
            df[c] = pd.Series([None] * len(df), index=df.index)

    for c in df.columns:
        df[c] = _as_series(df[c], index=df.index)

    for b in ["paye", "sms_envoye", "post_depart_envoye"]:
        df[b] = _to_bool_series(df[b])

    for n in ["prix_brut", "commissions", "frais_cb", "menage", "taxes_sejour", "nuitees", "charges", "%", "base"]:
        df[n] = _to_num(df[n]).fillna(0.0)

    df["date_arrivee"] = _to_date(df["date_arrivee"])
    df["date_depart"] = _to_date(df["date_depart"])

    mask_ok = pd.notna(df["date_arrivee"]) & pd.notna(df["date_depart"])
    if mask_ok.any():
        da = pd.to_datetime(df.loc[mask_ok, "date_arrivee"])
        dd = pd.to_datetime(df.loc[mask_ok, "date_depart"])
        df.loc[mask_ok, "nuitees"] = (dd - da).dt.days.clip(lower=0).astype(float)

    prix_brut = _to_num(df["prix_brut"])
    commissions = _to_num(df["commissions"])
    frais_cb = _to_num(df["frais_cb"])
    menage = _to_num(df["menage"])
    taxes = _to_num(df["taxes_sejour"])

    df["prix_net"] = (prix_brut - commissions - frais_cb).fillna(0.0)
    df["charges"] = (prix_brut - df["prix_net"]).fillna(0.0)
    df["base"] = (df["prix_net"] - menage - taxes).fillna(0.0)

    with np.errstate(divide="ignore", invalid="ignore"):
        pct = np.where(prix_brut > 0, (df["charges"] / prix_brut * 100), 0.0)
    df["%"] = pd.Series(pct, index=df.index).astype(float)

    miss_res = df["res_id"].isna() | (df["res_id"].astype(str).str.strip() == "")
    if miss_res.any():
        df.loc[miss_res, "res_id"] = [str(uuid.uuid4()) for _ in range(int(miss_res.sum()))]

    miss_uid = df["ical_uid"].isna() | (df["ical_uid"].astype(str).str.strip() == "")
    if miss_uid.any():
        df.loc[miss_uid, "ical_uid"] = df.loc[miss_uid].apply(build_stable_uid, axis=1)

    for c in ["nom_client", "plateforme", "telephone", "email"]:
        df[c] = df[c].astype(str).replace({"nan": "", "None": ""}).str.strip()

    return df[BASE_COLS]

def sauvegarder_donnees(df: pd.DataFrame) -> bool:
    try:
        out = ensure_schema(df).copy()
        for col in ["date_arrivee", "date_depart"]:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%d/%m/%Y")
        out.to_csv(CSV_RESERVATIONS, sep=";", index=False, encoding="utf-8")
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Erreur de sauvegarde : {e}")
        return False

def _df_to_xlsx_bytes(df: pd.DataFrame, sheet_name: str = "Reservations"):
    buf = BytesIO()
    try:
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name=sheet_name)
        return buf.getvalue(), None
    except Exception as e:
        st.warning(f"Impossible de générer le fichier Excel (openpyxl requis) : {e}")
        return None, e

def _format_phone_e164(phone: str) -> str:
    s = re.sub(r"\D", "", str(phone or ""))
    if not s:
        return ""
    if s.startswith("33"):
        return "+" + s
    if s.startswith("0"):
        return "+33" + s[1:]
    return "+" + s

# ============================== VUES ==============================
def vue_accueil(df, palette):
    st.header("🏠 Accueil")
    today = date.today()
    tomorrow = today + timedelta(days=1)
    st.write(f"**Aujourd'hui : {today.strftime('%d/%m/%Y')}**")

    dfv = df.copy()
    dfv["date_arrivee"] = _to_date(dfv["date_arrivee"])
    dfv["date_depart"] = _to_date(dfv["date_depart"])

    arr = dfv[dfv["date_arrivee"] == today][["nom_client", "telephone", "plateforme"]].copy()
    dep = dfv[dfv["date_depart"] == today][["nom_client", "telephone", "plateforme"]].copy()
    arr_plus1 = dfv[dfv["date_arrivee"] == tomorrow][["nom_client", "telephone", "plateforme"]].copy()

    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("🟢 Arrivées du jour")
        if arr.empty:
            st.info("Aucune arrivée.")
        else:
            st.dataframe(arr, use_container_width=True)
    with c2:
        st.subheader("🔴 Départs du jour")
        if dep.empty:
            st.info("Aucun départ.")
        else:
            st.dataframe(dep, use_container_width=True)
    with c3:
        st.subheader("🟠 Arrivées J+1 (demain)")
        if arr_plus1.empty:
            st.info("Aucune arrivée demain.")
        else:
            st.dataframe(arr_plus1, use_container_width=True)

def vue_reservations(df, palette):
    st.header("📋 Réservations")
    if df is None or df.empty:
        st.info("Aucune réservation.")
        return

    dfa = df.copy()
    dfa["date_arrivee_dt"] = pd.to_datetime(dfa["date_arrivee"], errors="coerce")

    years_avail = sorted(dfa["date_arrivee_dt"].dt.year.dropna().astype(int).unique().tolist(), reverse=True)
    months_avail = list(range(1, 13))
    plats_avail = sorted(
        dfa["plateforme"].dropna().astype(str).str.strip().replace({"": np.nan}).dropna().unique().tolist()
    )

    colf1, colf2, colf3 = st.columns(3)
    year = colf1.selectbox("Année", ["Toutes"] + years_avail, index=0)
    month = colf2.selectbox("Mois", ["Tous"] + months_avail, index=0)
    plat = colf3.selectbox("Plateforme", ["Toutes"] + plats_avail, index=0)

    data = dfa.copy()
    if year != "Toutes":
        data = data[data["date_arrivee_dt"].dt.year == int(year)]
    if month != "Tous":
        data = data[data["date_arrivee_dt"].dt.month == int(month)]
    if plat != "Toutes":
        data = data[data["plateforme"].astype(str).str.strip() == str(plat).strip()]

    brut = float(pd.to_numeric(data["prix_brut"], errors="coerce").fillna(0).sum())
    net = float(pd.to_numeric(data["prix_net"], errors="coerce").fillna(0).sum())
    base = float(pd.to_numeric(data["base"], errors="coerce").fillna(0).sum())
    nuits = int(pd.to_numeric(data["nuitees"], errors="coerce").fillna(0).sum())
    adr = (net / nuits) if nuits > 0 else 0.0
    charges = float(pd.to_numeric(data["charges"], errors="coerce").fillna(0).sum())

    html = f"""
    <div class='glass kpi-line'>
      <span class='chip'><small>Total brut</small><br><strong>{brut:,.2f} €</strong></span>
      <span class='chip'><small>Total net</small><br><strong>{net:,.2f} €</strong></span>
      <span class='chip'><small>Charges</small><br><strong>{charges:,.2f} €</strong></span>
      <span class='chip'><small>Base</small><br><strong>{base:,.2f} €</strong></span>
      <span class='chip'><small>Nuitées</small><br><strong>{nuits}</strong></span>
      <span class='chip'><small>ADR (net)</small><br><strong>{adr:,.2f} €</strong></span>
    </div>
    """.replace(",", " ")
    st.markdown(html, unsafe_allow_html=True)
    st.markdown("---")

    order_idx = pd.to_datetime(data["date_arrivee"], errors="coerce").sort_values(ascending=False).index
    data = data.loc[order_idx]
    st.dataframe(data.drop(columns=["date_arrivee_dt"]), use_container_width=True)

def vue_ajouter(df, palette):
    st.header("➕ Ajouter une réservation")
    with st.form("form_add", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            nom = st.text_input("Nom du client")
            email = st.text_input("Email", value="")
            tel = st.text_input("Téléphone")
            arr = st.date_input("Arrivée", date.today())
            dep = st.date_input("Départ", date.today() + timedelta(days=1))
        with c2:
            plat = st.selectbox("Plateforme", list(palette.keys()))
            brut = st.number_input("Prix brut (€)", min_value=0.0, step=0.01)
            commissions = st.number_input("Commissions (€)", min_value=0.0, step=0.01)
            frais_cb = st.number_input("Frais CB (€)", min_value=0.0, step=0.01)
            menage = st.number_input("Ménage (€)", min_value=0.0, step=0.01)
            taxes = st.number_input("Taxes séjour (€)", min_value=0.0, step=0.01)
            paye = st.checkbox("Payé", value=False)

        if st.form_submit_button("✅ Ajouter"):
            if not nom or dep <= arr:
                st.error("Veuillez entrer un nom et des dates valides.")
            else:
                nuitees = (dep - arr).days
                new = pd.DataFrame([{
                    "nom_client": nom, "email": email, "telephone": tel, "plateforme": plat,
                    "date_arrivee": arr, "date_depart": dep, "nuitees": nuitees,
                    "prix_brut": brut, "commissions": commissions, "frais_cb": frais_cb,
                    "menage": menage, "taxes_sejour": taxes, "paye": paye
                }])
                df2 = ensure_schema(pd.concat([df, new], ignore_index=True))
                if sauvegarder_donnees(df2):
                    st.success(f"Réservation pour {nom} ajoutée.")
                    st.rerun()

def vue_modifier(df, palette):
    st.header("✏️ Modifier / Supprimer")
    if df.empty:
        st.info("Aucune réservation.")
        return

    df_sorted = df.sort_values(by="date_arrivee", ascending=False).reset_index()
    options = [f"{i}: {r.get('nom_client', '')} ({r.get('date_arrivee', '')})" for i, r in df_sorted.iterrows()]
    sel = st.selectbox("Sélectionnez une réservation", options=options, index=None)

    if not sel:
        return

    idx = int(sel.split(":")[0])
    original_idx = df_sorted.loc[idx, "index"]
    row = df.loc[original_idx]

    with st.form(f"form_mod_{original_idx}"):
        c1, c2 = st.columns(2)
        with c1:
            nom = st.text_input("Nom", value=row.get("nom_client", "") or "")
            email = st.text_input("Email", value=row.get("email", "") or "")
            tel = st.text_input("Téléphone", value=row.get("telephone", "") or "")
            arrivee = st.date_input("Arrivée", value=row.get("date_arrivee"))
            depart = st.date_input("Départ", value=row.get("date_depart"))
        with c2:
            palette_keys = list(palette.keys())
            plat_idx = palette_keys.index(row.get("plateforme")) if row.get("plateforme") in palette_keys else 0
            plat = st.selectbox("Plateforme", options=palette_keys, index=plat_idx)
            paye = st.checkbox("Payé", value=bool(row.get("paye", False)))
            brut = float(pd.to_numeric(row.get("prix_brut"), errors="coerce") or 0)
            commissions = float(pd.to_numeric(row.get("commissions"), errors="coerce") or 0)
            frais_cb = float(pd.to_numeric(row.get("frais_cb"), errors="coerce") or 0)
            menage = float(pd.to_numeric(row.get("menage"), errors="coerce") or 0)
            taxes = float(pd.to_numeric(row.get("taxes_sejour"), errors="coerce") or 0)
            brut = st.number_input("Prix brut", min_value=0.0, step=0.01, value=brut)
            commissions = st.number_input("Commissions", min_value=0.0, step=0.01, value=commissions)
            frais_cb = st.number_input("Frais CB", min_value=0.0, step=0.01, value=frais_cb)
            menage = st.number_input("Ménage", min_value=0.0, step=0.01, value=menage)
            taxes = st.number_input("Taxes séjour", min_value=0.0, step=0.01, value=taxes)

        b1, b2 = st.columns([0.7, 0.3])
        if b1.form_submit_button("💾 Enregistrer"):
            for k, v in {
                "nom_client": nom, "email": email, "telephone": tel, "date_arrivee": arrivee, "date_depart": depart,
                "plateforme": plat, "paye": paye, "prix_brut": brut, "commissions": commissions, "frais_cb": frais_cb,
                "menage": menage, "taxes_sejour": taxes
            }.items():
                df.loc[original_idx, k] = v
            if sauvegarder_donnees(df):
                st.success("Modifié ✅")
                st.rerun()

        if b2.form_submit_button("🗑️ Supprimer"):
            df2 = df.drop(index=original_idx)
            if sauvegarder_donnees(df2):
                st.warning("Supprimé.")
                st.rerun()

def vue_plateformes(df, palette):
    st.header("🎨 Plateformes & couleurs")
    HAS_COLORCOL = hasattr(getattr(st, "column_config", object), "ColorColumn")
    plats_df = sorted(
        df.get("plateforme", pd.Series([], dtype=str))
        .astype(str).str.strip()
        .replace({"nan": ""})
        .dropna().unique().tolist()
    )
    all_plats = sorted(set(list(palette.keys()) + plats_df))
    base = pd.DataFrame({
        "plateforme": all_plats,
        "couleur": [palette.get(p, "#666666") for p in all_plats],
    })

    if HAS_COLORCOL:
        col_cfg = {
            "plateforme": st.column_config.TextColumn("Plateforme"),
            "couleur": st.column_config.ColorColumn("Couleur (hex)"),
        }
        help_txt = None
    else:
        col_cfg = {
            "plateforme": st.column_config.TextColumn("Plateforme"),
            "couleur": st.column_config.TextColumn(
                "Couleur (hex)",
                help="Ex: #1e90ff. Ta version de Streamlit ne supporte pas encore le sélecteur couleur.",
                validate=r"^#([0-9A-Fa-f]{6})$",
                width="small",
            ),
        }
        help_txt = "Aperçu affiché ci-dessous. Utilise un code hex valide (ex: #e74c3c)."

    edited = st.data_editor(
        base,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config=col_cfg,
    )

    if not HAS_COLORCOL and not edited.empty:
        st.caption(help_txt or "")
        chips = []
        for _, r in edited.iterrows():
            plat = str(r["plateforme"]).strip()
            col = str(r["couleur"]).strip()
            if not plat:
                continue
            chips.append(
                f"<span style='display:inline-block;margin:4px 6px;padding:6px 10px;"
                f"border-radius:12px;background:{col if re.match(r'^#([0-9A-Fa-f]{6})$', col) else '#666'};"
                f"color:#fff;'>{plat} {col}</span>"
            )
        if chips:
            st.markdown("".join(chips), unsafe_allow_html=True)

    c1, c2 = st.columns([0.6, 0.4])
    if c1.button("💾 Enregistrer la palette"):
        try:
            to_save = edited.copy()
            to_save["plateforme"] = to_save["plateforme"].astype(str).str.strip()
            to_save["couleur"] = to_save["couleur"].astype(str).str.strip()
            to_save = to_save[to_save["plateforme"] != ""].drop_duplicates(subset=["plateforme"])
            if not HAS_COLORCOL:
                ok = to_save["couleur"].str.match(r"^#([0-9A-Fa-f]{6})$")
                to_save.loc[~ok, "couleur"] = "#666666"
            to_save.to_csv(CSV_PLATEFORMES, sep=";", index=False, encoding="utf-8")
            st.success("Palette enregistrée ✅")
            st.rerun()
        except Exception as e:
            st.error(f"Erreur : {e}")

    if c2.button("↩️ Restaurer palette par défaut"):
        try:
            pd.DataFrame(list(DEFAULT_PALETTE.items()), columns=["plateforme", "couleur"]).to_csv(
                CSV_PLATEFORMES, sep=";", index=False, encoding="utf-8"
            )
            st.success("Palette par défaut restaurée.")
            st.rerun()
        except Exception as e:
            st.error(f"Erreur : {e}")

def vue_calendrier(df, palette):
    st.header("📅 Calendrier (grille mensuelle)")
    dfv = df.dropna(subset=['date_arrivee', 'date_depart']).copy()
    if dfv.empty:
        st.info("Aucune réservation à afficher.")
        return

    today = date.today()
    years = sorted(pd.to_datetime(dfv["date_arrivee"], errors="coerce").dt.year.dropna().astype(int).unique(), reverse=True)
    annee = st.selectbox("Année", options=years if years else [today.year], index=0)
    mois = st.selectbox("Mois", options=list(range(1, 13)), index=today.month - 1)

    st.markdown(
        "<div class='cal-header'><div>Lun</div><div>Mar</div><div>Mer</div><div>Jeu</div><div>Ven</div><div>Sam</div><div>Dim</div></div>",
        unsafe_allow_html=True
    )

    def day_resas(d):
        mask = (dfv['date_arrivee'] <= d) & (dfv['date_depart'] > d)
        return dfv[mask]

    cal = Calendar(firstweekday=0)
    html = ["<div class='cal-grid'>"]
    for week in cal.monthdatescalendar(annee, mois):
        for d in week:
            outside = (d.month != mois)
            classes = "cal-cell outside" if outside else "cal-cell"
            cell = f"<div class='{classes}'>"
            cell += f"<div class='cal-date'>{d.day}</div>"
            if not outside:
                rs = day_resas(d)
                if not rs.empty:
                    for _, r in rs.iterrows():
                        color = palette.get(r.get('plateforme'), '#888')
                        name = str(r.get('nom_client') or '')[:22]
                        cell += f"<div class='resa-pill' style='background:{color}' title='{r.get('nom_client','')}'>{name}</div>"
            cell += "</div>"
            html.append(cell)
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)
    st.markdown("---")

    st.subheader("Détail du mois sélectionné")
    debut_mois = date(annee, mois, 1)
    fin_mois = date(annee, mois, monthrange(annee, mois)[1])
    rows = dfv[(dfv['date_arrivee'] <= fin_mois) & (dfv['date_depart'] > debut_mois)].copy()

    if rows.empty:
        st.info("Aucune réservation sur ce mois.")
    else:
        plats = ["Toutes"] + sorted(rows["plateforme"].dropna().unique().tolist())
        plat = st.selectbox("Filtrer par plateforme", plats, index=0, key="cal_plat")
        if plat != "Toutes":
            rows = rows[rows["plateforme"] == plat]

        brut = float(pd.to_numeric(rows["prix_brut"], errors="coerce").fillna(0).sum())
        net = float(pd.to_numeric(rows["prix_net"], errors="coerce").fillna(0).sum())
        nuits = int(pd.to_numeric(rows["nuitees"], errors="coerce").fillna(0).sum())

        html = f"""
        <div class='glass kpi-line'>
          <span class='chip'><small>Total brut</small><br><strong>{brut:,.2f} €</strong></span>
          <span class='chip'><small>Total net</small><br><strong>{net:,.2f} €</strong></span>
          <span class='chip'><small>Nuitées</small><br><strong>{nuits}</strong></span>
        </div>
        """.replace(",", " ")
        st.markdown(html, unsafe_allow_html=True)
        st.dataframe(rows[["nom_client", "plateforme", "date_arrivee", "date_depart", "nuitees", "paye"]], use_container_width=True)

def vue_rapport(df, palette):
    st.header("📊 Rapport")
    if df is None or df.empty:
        st.info("Aucune donnée.")
        return

    dfa = df.copy()
    dfa["date_arrivee_dt"] = pd.to_datetime(dfa["date_arrivee"], errors="coerce")
    dfa["date_depart_dt"] = pd.to_datetime(dfa["date_depart"], errors="coerce")

    # Filtres
    years_avail = sorted(dfa["date_arrivee_dt"].dt.year.dropna().astype(int).unique().tolist(), reverse=True)
    months_avail = list(range(1, 13))
    plats_avail = sorted(
        dfa["plateforme"].astype(str).str.strip().replace({"": np.nan}).dropna().unique().tolist()
    )

    c1, c2, c3, c4 = st.columns([1, 1, 1, 1.2])
    year = c1.selectbox("Année", ["Toutes"] + years_avail, index=0)
    month = c2.selectbox("Mois", ["Tous"] + months_avail, index=0)
    plat = c3.selectbox("Plateforme", ["Toutes"] + plats_avail, index=0)
    metric = c4.selectbox("Métrique", ["prix_brut", "prix_net", "base", "charges", "menage", "taxes_sejour", "nuitees"], index=1)

    # Application des filtres
    data = dfa.copy()
    if year != "Toutes":
        data = data[data["date_arrivee_dt"].dt.year == int(year)]
    if month != "Tous":
        data = data[data["date_arrivee_dt"].dt.month == int(month)]
    if plat != "Toutes":
        data = data[data["plateforme"].astype(str).str.strip() == str(plat).strip()]

    if data.empty:
        st.warning("Aucune donnée après filtres.")
        return

    # ===== TAUX D'OCCUPATION =====
    st.markdown("---")
    st.subheader("📅 Taux d'occupation")

    # 1. Calcul des nuitées occupées par mois
    data["mois"] = data["date_arrivee_dt"].dt.to_period("M").astype(str)
    data["nuitees"] = (data["date_depart_dt"] - data["date_arrivee_dt"]).dt.days

    # 2. Nombre total de nuitées occupées par mois/plateforme
    occ_mois = data.groupby(["mois", "plateforme"], as_index=False)["nuitees"].sum()
    occ_mois.rename(columns={"nuitees": "nuitees_occupees"}, inplace=True)

    # 3. Nombre total de jours dans chaque mois (pour calculer le taux)
    def jours_dans_mois(periode_str):
        annee, mois = map(int, periode_str.split("-"))
        return monthrange(annee, mois)[1]

    occ_mois["jours_dans_mois"] = occ_mois["mois"].apply(jours_dans_mois)
    occ_mois["taux_occupation"] = (occ_mois["nuitees_occupees"] / occ_mois["jours_dans_mois"]) * 100

    # 4. Taux d'occupation global (toutes périodes confondues)
    total_nuitees_occupees = occ_mois["nuitees_occupees"].sum()
    total_jours_disponibles = occ_mois["jours_dans_mois"].sum()
    taux_global = (total_nuitees_occupees / total_jours_disponibles) * 100 if total_jours_disponibles > 0 else 0

    # ===== FILTRES SUPPLÉMENTAIRES POUR L'OCCUPATION =====
    st.markdown("---")
    col_plat, col_export = st.columns([1, 1])
    plat_occ = col_plat.selectbox("Filtrer par plateforme (occupation)", ["Toutes"] + plats_avail, index=0)

    # Application du filtre par plateforme pour l'occupation
    occ_filtered = occ_mois.copy()
    if plat_occ != "Toutes":
        occ_filtered = occ_filtered[occ_filtered["plateforme"] == plat_occ]

    # Recalcul du taux global après filtre
    filtered_nuitees = occ_filtered["nuitees_occupees"].sum()
    filtered_jours = occ_filtered["jours_dans_mois"].sum()
    taux_global_filtered = (filtered_nuitees / filtered_jours) * 100 if filtered_jours > 0 else 0

    # ===== AFFICHAGE =====
    st.markdown(
        f"""
        <div class='glass kpi-line'>
            <span class='chip'><small>Taux global</small><br><strong>{taux_global_filtered:.1f}%</strong></span>
            <span class='chip'><small>Nuitées occupées</small><br><strong>{filtered_nuitees}</strong></span>
            <span class='chip'><small>Jours disponibles</small><br><strong>{filtered_jours}</strong></span>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Export CSV/Excel
    occ_export = occ_filtered[["mois", "plateforme", "nuitees_occupees", "jours_dans_mois", "taux_occupation"]].copy()
    occ_export = occ_export.sort_values(["mois", "plateforme"], ascending=[False, True])

    csv_occ = occ_export.to_csv(index=False).encode("utf-8")
    col_export.download_button(
        "⬇️ Exporter les données d'occupation (CSV)",
        data=csv_occ,
        file_name="taux_occupation.csv",
        mime="text/csv"
    )

    xlsx_occ, _ = _df_to_xlsx_bytes(occ_export, "Taux d'occupation")
    if xlsx_occ:
        col_export.download_button(
            "⬇️ Exporter les données d'occupation (Excel)",
            data=xlsx_occ,
            file_name="taux_occupation.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    # Tableau détaillé
    st.dataframe(
        occ_export.assign(taux_occupation=lambda x: x["taux_occupation"].round(1)),
        use_container_width=True
    )

    # ===== COMPARAISON ENTRE ANNÉES =====
    st.markdown("---")
    st.subheader("📊 Comparaison des taux d'occupation par année")

    # Calcul des taux par année (CORRECTION ICI)
    data["annee"] = data["date_arrivee_dt"].dt.year  # Ajout de la colonne annee
    occ_annee = data.groupby(["annee", "plateforme"])["nuitees"].sum().reset_index()
    occ_annee.rename(columns={"nuitees": "nuitees_occupees"}, inplace=True)

    # Calcul du nombre de jours par année
    def jours_dans_annee(annee):
        return 366 if (annee % 4 == 0 and annee % 100 != 0) or (annee % 400 == 0) else 365

    occ_annee["jours_dans_annee"] = occ_annee["annee"].apply(jours_dans_annee)
    occ_annee["taux_occupation"] = (occ_annee["nuitees_occupees"] / occ_annee["jours_dans_annee"]) * 100

    # Filtre pour la comparaison
    annees_comparaison = st.multiselect(
        "Sélectionner les années à comparer",
        options=sorted(occ_annee["annee"].unique()),
        default=sorted(occ_annee["annee"].unique())[-2:]  # 2 dernières années par défaut
    )

    if not annees_comparaison:
        st.warning("Veuillez sélectionner au moins une année.")
    else:
        occ_comparaison = occ_annee[occ_annee["annee"].isin(annees_comparaison)].copy()

        # Graphique de comparaison
        try:
            chart_comparaison = alt.Chart(occ_comparaison).mark_bar().encode(
                x=alt.X("annee:N", title="Année"),
                y=alt.Y("taux_occupation:Q", title="Taux d'occupation (%)", scale=alt.Scale(domain=[0, 100])),
                color=alt.Color("plateforme:N", title="Plateforme"),
                tooltip=["annee", "plateforme", alt.Tooltip("taux_occupation:Q", format=".1f")]
            ).properties(
                height=400
            )
            st.altair_chart(chart_comparaison, use_container_width=True)
        except Exception as e:
            st.warning(f"Graphique de comparaison indisponible : {e}")

        # Tableau de comparaison
        st.dataframe(
            occ_comparaison[["annee", "plateforme", "nuitees_occupees", "taux_occupation"]]
            .sort_values(["annee", "plateforme"])
            .assign(taux_occupation=lambda x: x["taux_occupation"].round(1)),
            use_container_width=True
        )

    # ===== SUITE DU RAPPORT (MÉTRIQUES FINANCIÈRES) =====
    st.markdown("---")
    st.subheader("💰 Métriques financières")

    data["mois"] = data["date_arrivee_dt"].dt.to_period("M").astype(str)
    total_val = float(pd.to_numeric(data[metric], errors="coerce").fillna(0).sum())
    st.markdown(f"**Total {metric.replace('_', ' ')} : {total_val:,.2f}**".replace(",", " "))

    agg_mois = data.groupby("mois", as_index=False)[metric].sum().sort_values("mois")
    agg_mois_plat = data.groupby(["mois", "plateforme"], as_index=False)[metric].sum().sort_values(["mois", "plateforme"])

    with st.expander("Détail par mois", expanded=True):
        st.dataframe(agg_mois, use_container_width=True)

    with st.expander("Détail par mois et par plateforme", expanded=False):
        st.dataframe(agg_mois_plat, use_container_width=True)

    try:
        chart = alt.Chart(agg_mois_plat).mark_bar().encode(
            x=alt.X("mois:N", sort=None, title="Mois"),
            y=alt.Y(f"{metric}:Q", title=metric.replace("_", " ").title()),
            color=alt.Color("plateforme:N", title="Plateforme"),
            tooltip=["mois", "plateforme", alt.Tooltip(f"{metric}:Q", format=",.2f")]
        )
        st.altair_chart(chart.properties(height=420), use_container_width=True)
    except Exception as e:
        st.warning(f"Graphique indisponible : {e}")

    # Graphique du taux d'occupation
    st.markdown("---")
    st.subheader("📈 Évolution du taux d'occupation")

    try:
        chart_occ = alt.Chart(occ_filtered).mark_line(point=True).encode(
            x=alt.X("mois:N", sort=None, title="Mois"),
            y=alt.Y("taux_occupation:Q", title="Taux d'occupation (%)", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("plateforme:N", title="Plateforme"),
            tooltip=["mois", "plateforme", alt.Tooltip("taux_occupation:Q", format=".1f")]
        )
        st.altair_chart(chart_occ.properties(height=420), use_container_width=True)
    except Exception as e:
        st.warning(f"Graphique du taux d'occupation indisponible : {e}")

def _copy_button(label: str, payload: str, key: str):
    st.text_area("Aperçu", payload, height=200, key=f"ta_{key}")
    st.caption("Sélectionnez puis copiez (Ctrl/Cmd+C).")

def vue_sms(df, palette):
    st.header("✉️ SMS & WhatsApp")
    st.subheader("🛬 Pré-arrivée (arrivées J+1)")
    target_arrivee = st.date_input("Arrivées du", date.today() + timedelta(days=1), key="pre_date")
    pre = df.dropna(subset=["telephone", "nom_client", "date_arrivee"]).copy()
    pre["date_arrivee"] = _to_date(pre["date_arrivee"])
    pre["date_depart"] = _to_date(pre["date_depart"])
    sms_sent = _to_bool_series(pre["sms_envoye"])
    pre = pre[(pre["date_arrivee"] == target_arrivee) & (~sms_sent)]

    if pre.empty:
        st.info("Aucun client à contacter.")
    else:
        pre["_rowid"] = pre.index
        pre = pre.sort_values("date_arrivee").reset_index(drop=True)
        opts = [f"{i}: {r['nom_client']} ({r['telephone']})" for i, r in pre.iterrows()]
        pick = st.selectbox("Client (pré-arrivée)", options=opts, index=None)

        if pick:
            i = int(pick.split(":")[0])
            r = pre.loc[i]
            msg = (
                f"VILLA TOBIAS\n"
                f"Plateforme : {r.get('plateforme', 'N/A')}\n"
                f"Arrivée : {r['date_arrivee'].strftime('%d/%m/%Y')}  "
                f"Départ : {(r['date_depart'].strftime('%d/%m/%Y') if pd.notna(r['date_depart']) else '')}  "
                f"Nuitées : {int(pd.to_numeric(r.get('nuitees'), errors='coerce') or 0)}\n\n"
                f"Bonjour {r.get('nom_client')}\n"
                "Bienvenue chez nous ! \n\n "
                "Nous sommes ravis de vous accueillir bientôt à Nice. Afin d'organiser au mieux votre réception, "
                "nous vous demandons de bien vouloir remplir la fiche que vous trouverez en cliquant sur le lien suivant : \n"
                f"{FORM_SHORT_URL}\n\n"
                "Un parking est à votre disposition sur place.\n\n"
                "Le check-in se fait à partir de 14:00 h et le check-out avant 11:00 h. \n\n"
                "Vous trouverez des consignes à bagages dans chaque quartier, à Nice. \n\n"
                "Nous vous souhaitons un excellent voyage et nous nous réjouissons de vous rencontrer très bientôt. \n\n"
                "Annick & Charley \n\n"
                "****** \n\n"
                "Welcome to our establishment! \n\n"
                "We are delighted to welcome you soon to Nice. In order to organize your reception as efficiently as possible,"
                "we kindly ask you to fill out the form that you will find by clicking on the following link:"
                f" {FORM_SHORT_URL}\n\n"
                "Parking is available on site.\n\n"
                "Check-in is from 2:00 p.m. and check-out is before 11:00 a.m. \n\n"
                "You will find luggage storage facilities in every district of Nice. \n\n"
                "We wish you a pleasant journey and look forward to meeting you very soon.\n\n"
                "Annick & Charley"
            )
            enc = quote(msg)
            e164 = _format_phone_e164(r["telephone"])
            wa = re.sub(r"\D", "", e164)
            _copy_button("📋 Copier le message", msg, key=f"pre_{i}")

            c1, c2, c3 = st.columns(3)
            c1.link_button("📲 iPhone SMS", f"sms:&body={enc}")
            c2.link_button("🤖 Android SMS", f"sms:{e164}?body={enc}")
            c3.link_button("🟢 WhatsApp", f"https://wa.me/{wa}?text={enc}")

            if st.button("✅ Marquer 'SMS envoyé'", key=f"pre_mark_{r['_rowid']}"):
                df.loc[r["_rowid"], "sms_envoye"] = True
                if sauvegarder_donnees(df):
                    st.success("Marqué ✅")
                    st.rerun()

    st.markdown("---")
    st.subheader("📤 Post-départ (départs du jour)")
    target_depart = st.date_input("Départs du", date.today(), key="post_date")
    post = df.dropna(subset=["telephone", "nom_client", "date_depart"]).copy()
    post["date_depart"] = _to_date(post["date_depart"])
    post_sent = _to_bool_series(post["post_depart_envoye"])
    post = post[(post["date_depart"] == target_depart) & (~post_sent)]

    if post.empty:
        st.info("Aucun message à envoyer.")
    else:
        post["_rowid"] = post.index
        post = post.sort_values("date_depart").reset_index(drop=True)
        opts2 = [f"{i}: {r['nom_client']} — départ {r['date_depart']}" for i, r in post.iterrows()]
        pick2 = st.selectbox("Client (post-départ)", options=opts2, index=None)

        if pick2:
            j = int(pick2.split(":")[0])
            r2 = post.loc[j]
            name = str(r2.get("nom_client") or "").strip()
            msg2 = (
                f"Bonjour {name},\n\n"
                "Un grand merci d'avoir choisi notre appartement pour votre séjour.\n"
                "Nous espérons que vous avez passé un moment agréable.\n"
                "Si vous souhaitez revenir explorer encore un peu la ville, notre porte vous sera toujours grande ouverte.\n\n"
                "Au plaisir de vous accueillir à nouveau.\n\n"
                "Annick & Charley\n"
                f"\nHello {name},\n\n"
                "Thank you very much for choosing our apartment for your stay.\n"
                "We hope you had a great time — our door is always open if you want to come back.\n\n"
                "Annick & Charley"
            )
            enc2 = quote(msg2)
            e164b = _format_phone_e164(r2["telephone"])
            wab = re.sub(r"\D", "", e164b)
            _copy_button("📋 Copier le message", msg2, key=f"post_{j}")

            c1, c2, c3 = st.columns(3)
            c1.link_button("🟢 WhatsApp", f"https://wa.me/{wab}?text={enc2}")
            c2.link_button("📲 iPhone SMS", f"sms:&body={enc2}")
            c3.link_button("🤖 Android SMS", f"sms:{e164b}?body={enc2}")

            if st.button("✅ Marquer 'post-départ envoyé'", key=f"post_mark_{r2['_rowid']}"):
                df.loc[r2["_rowid"], "post_depart_envoye"] = True
                if sauvegarder_donnees(df):
                    st.success("Marqué ✅")
                    st.rerun()

def vue_export_ics(df, palette):
    st.header("📆 Export ICS (Google Calendar)")
    if df.empty:
        st.info("Aucune réservation.")
        return

    dfa = df.copy()
    dfa["date_arrivee_dt"] = pd.to_datetime(dfa["date_arrivee"], errors="coerce")
    years = sorted(dfa["date_arrivee_dt"].dt.year.dropna().astype(int).unique(), reverse=True)
    year = st.selectbox("Année (arrivées)", years if years else [date.today().year], index=0)
    plats = ["Tous"] + sorted(df["plateforme"].dropna().unique())
    plat = st.selectbox("Plateforme", plats, index=0)

    data = dfa[dfa["date_arrivee_dt"].dt.year == int(year)].copy()
    if plat != "Tous":
        data = data[data["plateforme"] == plat]

    if data.empty:
        st.warning("Rien à exporter.")
        return

    miss = data["ical_uid"].isna() | (data["ical_uid"].astype(str).str.strip() == "")
    if miss.any():
        data.loc[miss, "ical_uid"] = data.loc[miss].apply(build_stable_uid, axis=1)

    nowstamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    def _fmt(d):
        if isinstance(d, datetime):
            d = d.date()
        if isinstance(d, date):
            return f"{d.year:04d}{d.month:02d}{d.day:02d}"
        try:
            d2 = pd.to_datetime(d, errors="coerce")
            return d2.strftime("%Y%m%d")
        except Exception:
            return ""

    def _esc(s):
        if s is None:
            return ""
        return str(s).replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Villa Tobias//Reservations//FR", "CALSCALE:GREGORIAN"]
    for _, r in data.iterrows():
        dt_a = pd.to_datetime(r["date_arrivee"], errors="coerce")
        dt_d = pd.to_datetime(r["date_depart"], errors="coerce")
        if pd.isna(dt_a) or pd.isna(dt_d):
            continue

        summary = f"Villa Tobias — {r.get('nom_client', 'Sans nom')}"
        if r.get("plateforme"):
            summary += f" ({r['plateforme']})"

        desc = "\n".join([
            f"Client: {r.get('nom_client', '')}",
            f"Téléphone: {r.get('telephone', '')}",
            f"Nuitées: {int(pd.to_numeric(r.get('nuitees'), errors='coerce') or 0)}",
            f"Prix brut: {float(pd.to_numeric(r.get('prix_brut'), errors='coerce') or 0):.2f} €",
            f"res_id: {r.get('res_id', '')}",
        ])

        lines += [
            "BEGIN:VEVENT",
            f"UID:{r['ical_uid']}",
            f"DTSTAMP:{nowstamp}",
            f"DTSTART;VALUE=DATE:{_fmt(dt_a)}",
            f"DTEND;VALUE=DATE:{_fmt(dt_d)}",
            f"SUMMARY:{_esc(summary)}",
            f"DESCRIPTION:{_esc(desc)}",
            "TRANSP:OPAQUE",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    ics = "\r\n".join(lines) + "\r\n"
    st.download_button(
        "📥 Télécharger .ics",
        data=ics.encode("utf-8"),
        file_name=f"reservations_{year}.ics",
        mime="text/calendar"
    )

def vue_google_sheet(df, palette):
    st.header("📝 Fiche d'arrivée / Google Sheet")
    st.markdown(f"**Lien court à partager** : {FORM_SHORT_URL}")
    st.markdown(
        f'<iframe src="{GOOGLE_FORM_URL}" width="100%" height="900" frameborder="0"></iframe>',
        unsafe_allow_html=True
    )
    st.markdown("---")
    st.subheader("Feuille Google intégrée")
    st.markdown(
        f'<iframe src="{GOOGLE_SHEET_EMBED_URL}" width="100%" height="700" frameborder="0"></iframe>',
        unsafe_allow_html=True
    )
    st.markdown("---")
    st.subheader("Réponses (CSV publié)")
    try:
        rep = pd.read_csv(GOOGLE_SHEET_PUBLISHED_CSV)
        show_email = st.checkbox("Afficher les colonnes d'email (si présentes)", value=False)
        if not show_email:
            mask_cols = [c for c in rep.columns if "mail" in c.lower() or "email" in c.lower()]
            rep_display = rep.drop(columns=mask_cols, errors="ignore")
        else:
            rep_display = rep
        st.dataframe(rep_display, use_container_width=True)
    except Exception as e:
        st.error(f"Impossible de charger la feuille publiée : {e}")

def vue_clients(df, palette):
    st.header("👥 Liste des clients")
    if df.empty:
        st.info("Aucun client.")
        return

    clients = df[['nom_client', 'telephone', 'email', 'plateforme', 'res_id']].copy()
    for c in ["nom_client", "telephone", "email", "plateforme", "res_id"]:
        clients[c] = clients[c].astype(str).str.strip().replace({"nan": ""})

    clients = clients.loc[clients["nom_client"] != ""]
    clients = clients.drop_duplicates()
    clients = clients.sort_values(by="nom_client", kind="stable")
    st.dataframe(clients, use_container_width=True)

def vue_id(df, palette):
    st.header("🆔 Identifiants des réservations")
    if df is None or df.empty:
        st.info("Aucune réservation.")
        return

    tbl = df[["res_id", "nom_client", "telephone", "email", "plateforme"]].copy()
    for c in ["nom_client", "telephone", "email", "plateforme", "res_id"]:
        tbl[c] = tbl[c].astype(str).str.strip().replace({"nan": ""})

    tbl = tbl.dropna(subset=["res_id"])
    tbl = tbl[tbl["res_id"] != ""].drop_duplicates()
    st.dataframe(tbl, use_container_width=True)

# ============================== ADMIN ==============================
def admin_sidebar(df: pd.DataFrame):
    st.sidebar.markdown("---")
    st.sidebar.header("⚙️ Administration")

    try:
        out = ensure_schema(df).copy()
        for col in ["date_arrivee", "date_depart"]:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%d/%m/%Y")
        csv_bytes = out.to_csv(sep=";", index=False).encode("utf-8")
    except Exception:
        csv_bytes = b""

    st.sidebar.download_button(
        "⬇️ Télécharger CSV",
        data=csv_bytes,
        file_name="reservations.csv",
        mime="text/csv"
    )

    try:
        out_xlsx = ensure_schema(df).copy()
        for col in ["date_arrivee", "date_depart"]:
            out_xlsx[col] = pd.to_datetime(out_xlsx[col], errors="coerce").dt.strftime("%d/%m/%Y")
        xlsx_bytes, xlsx_err = _df_to_xlsx_bytes(out_xlsx, sheet_name="Reservations")
    except Exception as e:
        xlsx_bytes, xlsx_err = None, e

    st.sidebar.download_button(
        "⬇️ Télécharger XLSX",
        data=xlsx_bytes or b"",
        file_name="reservations.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=(xlsx_bytes is None),
        help="Génère un fichier Excel (.xlsx)"
    )

    if xlsx_bytes is None and xlsx_err:
        st.sidebar.caption("Astuce : ajoute **openpyxl** dans requirements.txt (ex: `openpyxl==3.1.5`).")

    up = st.sidebar.file_uploader("Restaurer (CSV ou XLSX)", type=["csv", "xlsx"], key="restore_uploader")

    if "restore_preview" not in st.session_state:
        st.session_state.restore_preview = None
        st.session_state.restore_source = ""

    if up is not None:
        try:
            if up.name.lower().endswith(".xlsx"):
                xls = pd.ExcelFile(up)
                sheet = st.sidebar.selectbox("Feuille Excel", xls.sheet_names, index=0, key="restore_sheet")
                tmp = pd.read_excel(xls, sheet_name=sheet, dtype=str)
                st.session_state.restore_source = f"XLSX — feuille « {sheet} »"
            else:
                raw = up.read()
                tmp = _detect_delimiter_and_read(raw)
                st.session_state.restore_source = "CSV"

            prev = ensure_schema(tmp)
            st.session_state.restore_preview = prev
            st.sidebar.success(f"Aperçu chargé ({st.session_state.restore_source})")

            with st.sidebar.expander("Aperçu (10 premières lignes)", expanded=False):
                st.dataframe(prev.head(10), use_container_width=True)
        except Exception as e:
            st.session_state.restore_preview = None
            st.sidebar.error(f"Erreur de lecture : {e}")

    if st.session_state.restore_preview is not None:
        if st.sidebar.button("✅ Confirmer la restauration"):
            try:
                save = st.session_state.restore_preview.copy()
                for col in ["date_arrivee", "date_depart"]:
                    save[col] = pd.to_datetime(save[col], errors="coerce").dt.strftime("%d/%m/%Y")
                save.to_csv(CSV_RESERVATIONS, sep=";", index=False, encoding="utf-8")
                st.cache_data.clear()
                st.sidebar.success("Fichier restauré — rechargement…")
                st.rerun()
            except Exception as e:
                st.sidebar.error(f"Erreur écriture : {e}")

    if st.sidebar.button("🧹 Vider le cache & recharger"):
        try:
            st.cache_data.clear()
        except Exception:
            pass
        st.sidebar.success("Cache vidé.")
        st.rerun()

# ============================== MAIN ==============================
def main():
    params = st.query_params
    if params.get("clear", ["0"])[0] in ("1", "true", "True", "yes"):
        try:
            st.cache_data.clear()
        except Exception:
            pass

    try:
        mode_clair = st.sidebar.toggle("🌓 Mode clair (PC)", value=False)
    except Exception:
        mode_clair = st.sidebar.checkbox("🌓 Mode clair (PC)", value=False)

    apply_style(light=bool(mode_clair))
    st.title("✨ Villa Tobias — Gestion des Réservations")

    df, palette_loaded = charger_donnees()
    palette = palette_loaded if palette_loaded else DEFAULT_PALETTE

    pages = {
        "🏠 Accueil": vue_accueil,
        "📋 Réservations": vue_reservations,
        "➕ Ajouter": vue_ajouter,
        "✏️ Modifier / Supprimer": vue_modifier,
        "🎨 Plateformes": vue_plateformes,
        "📅 Calendrier": vue_calendrier,
        "📊 Rapport": vue_rapport,
        "✉️ SMS": vue_sms,
        "📆 Export ICS": vue_export_ics,
        "📝 Google Sheet": vue_google_sheet,
        "👥 Clients": vue_clients,
        "🆔 ID": vue_id,
    }

    choice = st.sidebar.radio("Aller à", list(pages.keys()))
    pages[choice](df, palette)
    admin_sidebar(df)

if __name__ == "__main__":
    main()
