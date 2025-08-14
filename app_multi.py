# app_multi.py — Villa Tobias (multi-appartements)
# Corrections :
# - Plateformes dynamiques partout (plus de liste figée)
# - Gestion palette plateformes/couleurs (ajout/suppression) fiable
# - Calendrier/graphes utilisent les couleurs de la palette
# - Téléphone en texte (+33 conservé, pas de .0)
# - SMS manuel, Export ICS, etc. (inchangés)

import streamlit as st
import pandas as pd
import numpy as np
import calendar
import matplotlib.pyplot as plt
from datetime import date, timedelta, datetime, timezone
from io import BytesIO
from pathlib import Path
import hashlib
import os
from urllib.parse import quote

# ==============================  CONSTANTES  ===============================

DATA_FILE = "reservations_multi_modele.xlsx"
PLATFORM_FILE = "platform_palette.xlsx"

DEFAULT_PALETTE = [
    "#1f77b4", "#2ca02c", "#ff7f0e", "#d62728", "#9467bd", "#8c564b",
    "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"
]

# ==============================  OUTILS  ===================================

def to_date_only(x):
    if pd.isna(x) or x is None:
        return None
    try:
        return pd.to_datetime(x).date()
    except Exception:
        return None

def format_date_str(d):
    return d.strftime("%Y/%m/%d") if isinstance(d, date) else ""

def normalize_tel(x):
    """Force lecture téléphone en TEXTE, retire espaces et .0, conserve +."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = str(x).strip().replace(" ", "")
    if s.endswith(".0"):
        s = s[:-2]
    return s

def ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    base_cols = [
        "appartement", "nom_client", "plateforme", "telephone",
        "date_arrivee", "date_depart", "nuitees",
        "prix_brut", "prix_net", "charges", "%",
        "AAAA", "MM", "ical_uid", "sms_status"
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=base_cols)

    df = df.copy()

    # Dates
    for c in ["date_arrivee", "date_depart"]:
        if c in df.columns:
            df[c] = df[c].apply(to_date_only)

    # Numériques
    for c in ["prix_brut","prix_net","charges","%","nuitees","AAAA","MM"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Charges / %
    if "prix_brut" in df.columns and "prix_net" in df.columns:
        if "charges" not in df.columns:
            df["charges"] = df["prix_brut"] - df["prix_net"]
        if "%" not in df.columns:
            with pd.option_context("mode.use_inf_as_na", True):
                df["%"] = (df["charges"] / df["prix_brut"] * 100).fillna(0)
    for c in ["prix_brut","prix_net","charges","%"]:
        if c in df.columns:
            df[c] = df[c].round(2)

    # Nuitées
    if "date_arrivee" in df.columns and "date_depart" in df.columns:
        df["nuitees"] = [
            (d2 - d1).days if (isinstance(d1, date) and isinstance(d2, date)) else np.nan
            for d1, d2 in zip(df["date_arrivee"], df["date_depart"])
        ]

    # AAAA / MM
    if "date_arrivee" in df.columns:
        df["AAAA"] = df["date_arrivee"].apply(lambda d: d.year if isinstance(d, date) else np.nan).astype("Int64")
        df["MM"]   = df["date_arrivee"].apply(lambda d: d.month if isinstance(d, date) else np.nan).astype("Int64")

    # Champs par défaut
    defaults = {
        "appartement": "", "nom_client": "", "plateforme": "Autre",
        "telephone": "", "ical_uid": "", "sms_status": ""  # sms_status: "🟧 attente" / "🟩 envoyé"
    }
    for k, v in defaults.items():
        if k not in df.columns:
            df[k] = v

    # Tel propre
    df["telephone"] = df["telephone"].apply(normalize_tel)

    cols = base_cols
    return df[[c for c in cols if c in df.columns] + [c for c in df.columns if c not in cols]]

def is_total_row(row: pd.Series) -> bool:
    name_is_total = str(row.get("nom_client","")).strip().lower() == "total"
    pf_is_total   = str(row.get("plateforme","")).strip().lower() == "total"
    app_is_total  = str(row.get("appartement","")).strip().lower() == "total"
    d1 = row.get("date_arrivee"); d2 = row.get("date_depart")
    no_dates = not isinstance(d1, date) and not isinstance(d2, date)
    has_money = any(pd.notna(row.get(c)) and float(row.get(c) or 0) != 0 for c in ["prix_brut","prix_net","charges"])
    return name_is_total or pf_is_total or app_is_total or (no_dates and has_money)

def split_totals(df: pd.DataFrame):
    if df is None or df.empty:
        return df, df
    mask = df.apply(is_total_row, axis=1)
    return df[~mask].copy(), df[mask].copy()

def sort_core(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    by = [c for c in ["appartement","date_arrivee","nom_client"] if c in df.columns]
    return df.sort_values(by=by, na_position="last").reset_index(drop=True)

# ==============================  PALETTE PLATEFORMES  ======================

def default_platforms_seed():
    return pd.DataFrame({
        "plateforme": ["Booking", "Airbnb", "Autre"],
        "color": ["#1f77b4", "#2ca02c", "#ff7f0e"]
    })

def load_platform_palette() -> pd.DataFrame:
    p = Path(PLATFORM_FILE)
    if not p.exists():
        df = default_platforms_seed()
        df.to_excel(PLATFORM_FILE, index=False)
        return df
    try:
        df = pd.read_excel(PLATFORM_FILE)
        df["plateforme"] = df["plateforme"].astype(str).str.strip()
        df["color"] = df["color"].astype(str).str.strip()
        df = df.dropna(subset=["plateforme","color"]).drop_duplicates(subset=["plateforme"], keep="last")
        return df
    except Exception:
        return default_platforms_seed()

def save_platform_palette(df_palette: pd.DataFrame):
    df = df_palette.copy()
    df["plateforme"] = df["plateforme"].astype(str).str.strip()
    df["color"] = df["color"].astype(str).str.strip()
    df = df.dropna(subset=["plateforme","color"]).drop_duplicates(subset=["plateforme"], keep="last")
    with pd.ExcelWriter(PLATFORM_FILE, engine="openpyxl") as w:
        df.to_excel(w, index=False)

def build_platform_cmap(df_palette: pd.DataFrame):
    cmap = {}
    free = [c for c in DEFAULT_PALETTE]
    for _, r in df_palette.iterrows():
        pf = str(r["plateforme"]).strip()
        col = str(r["color"]).strip()
        if pf and col:
            cmap[pf] = col
    def get_color(pf_name: str):
        if pf_name in cmap:
            return cmap[pf_name]
        col = free[len(cmap) % len(free)]
        cmap[pf_name] = col
        return col
    return get_color

def platform_options(df_resa: pd.DataFrame, df_palette: pd.DataFrame):
    """Liste dynamique : plateformes présentes dans les données ∪ palette."""
    from_data = sorted(df_resa["plateforme"].dropna().astype(str).str.strip().unique().tolist()) if "plateforme" in df_resa.columns else []
    from_palette = sorted(df_palette["plateforme"].dropna().astype(str).str.strip().unique().tolist())
    merged = sorted({*from_data, *from_palette})
    if not merged:
        merged = ["Autre"]
    return merged

def sidebar_platform_manager():
    st.sidebar.markdown("---")
    st.sidebar.subheader("🎨 Plateformes & couleurs")
    df_pal = load_platform_palette()

    # Liste existante
    if df_pal.empty:
        st.sidebar.info("Aucune plateforme définie.")
    else:
        for i, r in df_pal.reset_index(drop=True).iterrows():
            pf = r["plateforme"]; col = r["color"]
            c1, c2, c3 = st.sidebar.columns([2, 2, 1])
            with c1:
                st.text_input("Nom", value=pf, key=f"pf_name_{i}", label_visibility="collapsed", disabled=True)
            with c2:
                new_col = st.color_picker("Couleur", value=col, key=f"pf_color_{i}", label_visibility="collapsed")
                if new_col != col:
                    df_pal.loc[df_pal["plateforme"] == pf, "color"] = new_col
                    save_platform_palette(df_pal)
                    st.rerun()
            with c3:
                if st.button("🗑", key=f"pf_del_{i}"):
                    df_new = df_pal[df_pal["plateforme"] != pf]
                    save_platform_palette(df_new)
                    st.rerun()

    # Ajout
    st.sidebar.markdown("**Ajouter une plateforme**")
    new_pf = st.sidebar.text_input("Nom de la plateforme", key="pf_add_name", placeholder="Ex: Abritel")
    new_color = st.sidebar.color_picker("Couleur", "#bcbd22", key="pf_add_color")
    if st.sidebar.button("➕ Ajouter"):
        pf_clean = (new_pf or "").strip()
        if not pf_clean:
            st.sidebar.warning("Indique un nom de plateforme.")
        else:
            if (df_pal["plateforme"].str.lower() == pf_clean.lower()).any():
                st.sidebar.info("Cette plateforme existe déjà.")
            else:
                df_new = pd.concat([df_pal, pd.DataFrame([{"plateforme": pf_clean, "color": new_color}])], ignore_index=True)
                save_platform_palette(df_new)
                st.sidebar.success(f"Plateforme « {pf_clean} » ajoutée.")
                st.rerun()

# ==============================  EXCEL I/O  ================================

@st.cache_data(show_spinner=False)
def _read_excel_cached(path: str, mtime: float):
    return pd.read_excel(path, converters={"telephone": normalize_tel})

def charger_donnees() -> pd.DataFrame:
    if not os.path.exists(DATA_FILE):
        # Modèle minimal si absent
        df0 = pd.DataFrame(columns=[
            "appartement","nom_client","plateforme","telephone",
            "date_arrivee","date_depart","nuitees",
            "prix_brut","prix_net","charges","%",
            "AAAA","MM","ical_uid","sms_status"
        ])
        with pd.ExcelWriter(DATA_FILE, engine="openpyxl") as w:
            df0.to_excel(w, index=False)
    try:
        mtime = os.path.getmtime(DATA_FILE)
        df = _read_excel_cached(DATA_FILE, mtime)
        return ensure_schema(df)
    except Exception as e:
        st.error(f"Erreur de lecture Excel : {e}")
        return ensure_schema(pd.DataFrame())

def _force_telephone_text_format_openpyxl(writer, df_to_save: pd.DataFrame, sheet_name: str):
    try:
        ws = writer.sheets.get(sheet_name) or writer.sheets.get("Sheet1")
        if ws is None or "telephone" not in df_to_save.columns:
            return
        col_idx = df_to_save.columns.get_loc("telephone") + 1
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=col_idx, max_col=col_idx):
            row[0].number_format = '@'
    except Exception:
        pass

def sauvegarder_donnees(df: pd.DataFrame):
    df = ensure_schema(df)
    core, totals = split_totals(df)
    core = sort_core(core)
    out = pd.concat([core, totals], ignore_index=True)
    try:
        with pd.ExcelWriter(DATA_FILE, engine="openpyxl") as w:
            out.to_excel(w, index=False, sheet_name="Sheet1")
            _force_telephone_text_format_openpyxl(w, out, "Sheet1")
        st.cache_data.clear()
        st.success("💾 Sauvegarde Excel effectuée.")
    except Exception as e:
        st.error(f"Échec de sauvegarde Excel : {e}")

def bouton_restaurer():
    up = st.sidebar.file_uploader("📤 Restauration xlsx", type=["xlsx"], help="Remplace le fichier actuel")
    if up is not None:
        try:
            df_new = pd.read_excel(up, converters={"telephone": normalize_tel})
            df_new = ensure_schema(df_new)
            sauvegarder_donnees(df_new)
            st.sidebar.success("✅ Fichier restauré.")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Erreur import: {e}")

def bouton_telecharger(df: pd.DataFrame):
    buf = BytesIO()
    try:
        ensure_schema(df).to_excel(buf, index=False, engine="openpyxl")
        data_xlsx = buf.getvalue()
    except Exception as e:
        st.sidebar.error(f"Export XLSX indisponible : {e}")
        data_xlsx = None
    st.sidebar.download_button(
        "💾 Sauvegarde xlsx",
        data=data_xlsx if data_xlsx else b"",
        file_name=DATA_FILE,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=(data_xlsx is None),
    )

def render_cache_section_sidebar():
    st.sidebar.markdown("---")
    st.sidebar.subheader("🧰 Maintenance")
    if st.sidebar.button("♻️ Vider le cache et relancer"):
        try: st.cache_data.clear()
        except Exception: pass
        try: st.cache_resource.clear()
        except Exception: pass
        st.sidebar.success("Cache vidé. Redémarrage…")
        st.rerun()

# ==============================  ICS EXPORT  ================================

def ics_escape(text: str) -> str:
    if text is None:
        return ""
    s = str(text)
    s = s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    return s

def fmt_date_ics(d: date) -> str:
    return d.strftime("%Y%m%d")

def dtstamp_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def stable_uid(nom_client, plateforme, d1, d2, tel, app, salt="v1"):
    base = f"{app}|{nom_client}|{plateforme}|{d1}|{d2}|{tel}|{salt}"
    h = hashlib.sha1(base.encode("utf-8")).hexdigest()
    return f"vtm-{h}@villatobias"

def df_to_ics(df: pd.DataFrame, cal_name: str = "Villa Tobias – Réservations (multi)") -> str:
    df = ensure_schema(df)
    if df.empty:
        return (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Villa Tobias//Reservations Multi//FR\r\n"
            f"X-WR-CALNAME:{ics_escape(cal_name)}\r\n"
            "CALSCALE:GREGORIAN\r\n"
            "METHOD:PUBLISH\r\n"
            "END:VCALENDAR\r\n"
        )
    core, _ = split_totals(df)
    core = sort_core(core)

    lines = []
    A = lines.append
    A("BEGIN:VCALENDAR")
    A("VERSION:2.0")
    A("PRODID:-//Villa Tobias//Reservations Multi//FR")
    A(f"X-WR-CALNAME:{ics_escape(cal_name)}")
    A("CALSCALE:GREGORIAN")
    A("METHOD:PUBLISH")

    for _, r in core.iterrows():
        d1, d2 = r.get("date_arrivee"), r.get("date_depart")
        if not (isinstance(d1, date) and isinstance(d2, date)):
            continue
        pf = str(r.get("plateforme","")).strip()
        nom = str(r.get("nom_client","")).strip()
        tel = str(r.get("telephone","")).strip()
        app = str(r.get("appartement","")).strip()
        brut = float(r.get("prix_brut") or 0)
        net  = float(r.get("prix_net") or 0)
        nuitees = int(r.get("nuitees") or (d2 - d1).days)

        summary = " - ".join([x for x in [app, pf, nom, tel] if x])
        desc = (
            f"App: {app}\\nPlateforme: {pf}\\nClient: {nom}\\nTel: {tel}\\n"
            f"Arrivee: {d1.strftime('%Y/%m/%d')}\\nDepart: {d2.strftime('%Y/%m/%d')}\\n"
            f"Nuitees: {nuitees}\\nBrut: {brut:.2f} €\\nNet: {net:.2f} €"
        )

        uid_existing = str(r.get("ical_uid") or "").strip()
        uid = uid_existing if uid_existing else stable_uid(nom, pf, d1, d2, tel, app)

        A("BEGIN:VEVENT")
        A(f"UID:{ics_escape(uid)}")
        A(f"DTSTAMP:{dtstamp_utc_now()}")
        A(f"DTSTART;VALUE=DATE:{fmt_date_ics(d1)}")
        A(f"DTEND;VALUE=DATE:{fmt_date_ics(d2)}")
        A(f"SUMMARY:{ics_escape(summary)}")
        A(f"DESCRIPTION:{ics_escape(desc)}")
        A("END:VEVENT")

    A("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

# ==============================  VUES  =====================================

def totaux_html(total_brut, total_net, total_chg, total_nuits, pct_moy):
    return f"""
<style>
.chips-wrap {{ display:flex; flex-wrap:wrap; gap:10px; margin:8px 0 16px 0; }}
.chip {{ padding:8px 10px; border-radius:10px; background: rgba(127,127,127,0.12); border: 1px solid rgba(127,127,127,0.25); }}
.chip b {{ display:block; margin-bottom:4px; }}
</style>
<div class="chips-wrap">
  <div class="chip"><b>Total Brut</b><div>{total_brut:,.2f} €</div></div>
  <div class="chip"><b>Total Net</b><div>{total_net:,.2f} €</div></div>
  <div class="chip"><b>Total Charges</b><div>{total_chg:,.2f} €</div></div>
  <div class="chip"><b>Total Nuitées</b><div>{int(total_nuits) if pd.notna(total_nuits) else 0}</div></div>
  <div class="chip"><b>Commission moy.</b><div>{pct_moy:.2f} %</div></div>
</div>
"""

def vue_reservations(df: pd.DataFrame):
    st.title("📋 Réservations (Multi)")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    df_pal = load_platform_palette()

    # Filtres haut : Appartement, Plateforme, Année, Mois
    c1, c2, c3, c4 = st.columns(4)
    apps = ["Tous"] + sorted(df["appartement"].dropna().astype(str).unique().tolist())
    app = c1.selectbox("Appartement", apps, index=0)

    plats_dyn = platform_options(df, df_pal)
    pf = c2.selectbox("Plateforme", ["Toutes"] + plats_dyn, index=0)

    annees = ["Toutes"] + sorted(df["AAAA"].dropna().astype(int).unique().tolist())
    an = c3.selectbox("Année", annees, index=len(annees)-1 if len(annees)>1 else 0)
    mois = ["Tous"] + [f"{i:02d}" for i in range(1,13)]
    mo = c4.selectbox("Mois", mois, index=0)

    data = df.copy()
    if app != "Tous":
        data = data[data["appartement"] == app]
    if pf != "Toutes":
        data = data[data["plateforme"].str.lower() == pf.lower()]
    if an != "Toutes":
        data = data[data["AAAA"] == int(an)]
    if mo != "Tous":
        data = data[data["MM"] == int(mo)]

    core, totals = split_totals(data)
    core = sort_core(core)

    # Totaux (sur core uniquement)
    if not core.empty:
        total_brut   = core["prix_brut"].sum(skipna=True)
        total_net    = core["prix_net"].sum(skipna=True)
        total_chg    = core["charges"].sum(skipna=True)
        total_nuits  = core["nuitees"].sum(skipna=True)
        pct_moy = (core["charges"].sum() / core["prix_brut"].sum() * 100) if core["prix_brut"].sum() else 0
        st.markdown(totaux_html(total_brut, total_net, total_chg, total_nuits, pct_moy), unsafe_allow_html=True)

    show = pd.concat([core, totals], ignore_index=True)
    for c in ["date_arrivee","date_depart"]:
        show[c] = show[c].apply(format_date_str)
    st.dataframe(show, use_container_width=True)

def vue_ajouter(df: pd.DataFrame):
    st.title("➕ Ajouter une réservation")
    st.caption("Saisie rapide (libellés en ligne)")

    df_pal = load_platform_palette()
    plats_dyn = platform_options(df, df_pal)

    def inline(label, widget, key=None, **kwargs):
        a,b = st.columns([1,2])
        with a: st.markdown(f"**{label}**")
        with b: return widget(label, key=key, label_visibility="collapsed", **kwargs)

    app = inline("Appartement", st.text_input, key="add_app", value="")
    nom = inline("Nom", st.text_input, key="add_nom", value="")
    tel = inline("Téléphone (+33...)", st.text_input, key="add_tel", value="")
    pf  = inline("Plateforme", st.selectbox, key="add_pf", options=plats_dyn, index=0)

    arrivee = inline("Arrivée", st.date_input, key="add_arr", value=date.today())
    min_dep = arrivee + timedelta(days=1)
    depart = inline("Départ", st.date_input, key="add_dep", value=min_dep, min_value=min_dep)

    brut = inline("Prix brut (€)", st.number_input, key="add_brut", min_value=0.0, step=1.0, format="%.2f")
    net  = inline("Prix net (€)",  st.number_input, key="add_net",  min_value=0.0, step=1.0, format="%.2f")
    charges = max(float(brut) - float(net), 0.0)
    pct = (charges / float(brut) * 100) if float(brut) > 0 else 0.0
    inline("Charges (€)", st.number_input, key="add_ch", value=round(charges,2), step=0.01, format="%.2f", disabled=True)
    inline("Commission (%)", st.number_input, key="add_pct", value=round(pct,2), step=0.01, format="%.2f", disabled=True)

    c1, c2 = st.columns(2)
    if c1.button("Enregistrer"):
        if net > brut:
            st.error("Le prix net ne peut pas être supérieur au prix brut.")
            return
        if depart < arrivee + timedelta(days=1):
            st.error("La date de départ doit être au moins le lendemain de l’arrivée.")
            return
        row = {
            "appartement": (app or "").strip(),
            "nom_client": (nom or "").strip(),
            "plateforme": pf,
            "telephone": normalize_tel(tel),
            "date_arrivee": arrivee,
            "date_depart": depart,
            "prix_brut": float(brut),
            "prix_net": float(net),
            "charges": round(charges,2),
            "%": round(pct,2),
            "nuitees": (depart - arrivee).days,
            "AAAA": arrivee.year,
            "MM": arrivee.month,
            "ical_uid": "",
            "sms_status": "🟧 attente"
        }
        df2 = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        sauvegarder_donnees(df2)
        st.success("✅ Réservation enregistrée")
        st.rerun()
    c2.info("Astuce : la date de départ est proposée au lendemain.")

def vue_modifier(df: pd.DataFrame):
    st.title("✏️ Modifier / Supprimer")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune réservation.")
        return

    df_pal = load_platform_palette()
    plats_dyn = platform_options(df, df_pal)

    df["id_aff"] = df["appartement"].astype(str) + " | " + df["nom_client"].astype(str) + " | " + df["date_arrivee"].apply(format_date_str)
    choix = st.selectbox("Choisir une réservation", df["id_aff"])

    sel = df.index[df["id_aff"] == choix]
    if len(sel) == 0:
        st.warning("Sélection invalide.")
        return
    i = sel[0]

    cA, cB = st.columns(2)
    app = cA.text_input("Appartement", df.at[i,"appartement"])
    nom = cB.text_input("Nom", df.at[i,"nom_client"])
    tel = st.text_input("Téléphone", normalize_tel(df.at[i,"telephone"]))
    # plateforme dynamique
    pf_curr = df.at[i,"plateforme"] if pd.notna(df.at[i,"plateforme"]) else "Autre"
    init_idx = plats_dyn.index(pf_curr) if pf_curr in plats_dyn else 0
    pf = st.selectbox("Plateforme", plats_dyn, index=init_idx)

    arr = st.date_input("Arrivée", df.at[i,"date_arrivee"] if isinstance(df.at[i,"date_arrivee"], date) else date.today())
    dep = st.date_input("Départ", df.at[i,"date_depart"] if isinstance(df.at[i,"date_depart"], date) else arr+timedelta(days=1), min_value=arr+timedelta(days=1))

    c1, c2, c3 = st.columns(3)
    brut = c1.number_input("Prix brut (€)", value=float(df.at[i,"prix_brut"]) if pd.notna(df.at[i,"prix_brut"]) else 0.0, min_value=0.0, step=1.0, format="%.2f")
    net  = c2.number_input("Prix net (€)",  value=float(df.at[i,"prix_net"])  if pd.notna(df.at[i,"prix_net"]) else 0.0, min_value=0.0, step=1.0, format="%.2f")
    charges = max(brut - net, 0.0)
    pct = (charges / brut * 100) if brut > 0 else 0.0
    c3.markdown(f"**Charges** : {charges:.2f} €  \n**%** : {pct:.2f}")

    c4, c5 = st.columns(2)
    if c4.button("💾 Enregistrer"):
        if dep < arr + timedelta(days=1):
            st.error("La date de départ doit être au moins le lendemain de l’arrivée.")
            return
        df.at[i,"appartement"] = app.strip()
        df.at[i,"nom_client"] = nom.strip()
        df.at[i,"plateforme"] = pf
        df.at[i,"telephone"] = normalize_tel(tel)
        df.at[i,"date_arrivee"] = arr
        df.at[i,"date_depart"] = dep
        df.at[i,"prix_brut"] = float(brut)
        df.at[i,"prix_net"] = float(net)
        df.at[i,"charges"] = round(charges,2)
        df.at[i,"%"] = round(pct,2)
        df.at[i,"nuitees"] = (dep - arr).days
        df.at[i,"AAAA"] = arr.year
        df.at[i,"MM"] = arr.month
        df.drop(columns=["id_aff"], inplace=True, errors="ignore")
        sauvegarder_donnees(df)
        st.success("✅ Modifié")
        st.rerun()

    if c5.button("🗑 Supprimer"):
        df2 = df.drop(index=i)
        df2.drop(columns=["id_aff"], inplace=True, errors="ignore")
        sauvegarder_donnees(df2)
        st.warning("Supprimé.")
        st.rerun()

def vue_calendrier(df: pd.DataFrame):
    st.title("📅 Calendrier mensuel")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    c0, c1, c2 = st.columns(3)
    apps = ["Tous"] + sorted(df["appartement"].dropna().astype(str).unique().tolist())
    app = c0.selectbox("Appartement", apps, index=0)
    mois_nom = c1.selectbox("Mois", list(calendar.month_name)[1:], index=max(0, date.today().month-1))
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    if not annees:
        st.warning("Aucune année disponible.")
        return
    annee = c2.selectbox("Année", annees, index=len(annees)-1)

    data = df.copy()
    if app != "Tous":
        data = data[data["appartement"] == app]

    cmap_fn = build_platform_cmap(load_platform_palette())

    mois_index = list(calendar.month_name).index(mois_nom)
    nb_jours = calendar.monthrange(annee, mois_index)[1]
    days = [date(annee, mois_index, j+1) for j in range(nb_jours)]
    planning = {d: [] for d in days}

    def badge(text, color):
        return f"<span style='display:inline-block;padding:2px 6px;border-radius:6px;background:{color};color:white;font-size:12px;'>{text}</span>"

    core, _ = split_totals(data)
    for _, r in core.iterrows():
        d1, d2 = r.get("date_arrivee"), r.get("date_depart")
        if not (isinstance(d1, date) and isinstance(d2, date)): 
            continue
        for d in days:
            if d1 <= d < d2:
                pf = str(r.get("plateforme","Autre"))
                col = cmap_fn(pf)
                nom = str(r.get("nom_client",""))
                planning[d].append(badge(pf, col) + " " + nom)

    weeks = calendar.monthcalendar(annee, mois_index)
    html = "<table style='width:100%;border-collapse:collapse;font-size:13px;'>"
    head = ["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"]
    html += "<tr>" + "".join([f"<th style='text-align:left;padding:6px;border-bottom:1px solid #444;'>{h}</th>" for h in head]) + "</tr>"
    for w in weeks:
        html += "<tr>"
        for j in w:
            if j == 0:
                html += "<td style='vertical-align:top;padding:6px;border-bottom:1px solid #333;'></td>"
            else:
                d = date(annee, mois_index, j)
                items = "<br>".join(planning.get(d, []))
                html += f"<td style='vertical-align:top;padding:6px;border-bottom:1px solid #333;'><b>{j}</b><br>{items}</td>"
        html += "</tr>"
    html += "</table>"
    st.markdown(html, unsafe_allow_html=True)

def vue_rapport(df: pd.DataFrame):
    st.title("📊 Rapport")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    df_pal = load_platform_palette()
    plats_dyn = platform_options(df, df_pal)

    c0, c1, c2, c3 = st.columns(4)
    apps = ["Tous"] + sorted(df["appartement"].dropna().astype(str).unique().tolist())
    app = c0.selectbox("Appartement", apps, index=0)
    annees = sorted(df["AAAA"].dropna().astype(int).unique().tolist())
    if not annees:
        st.info("Aucune année disponible.")
        return
    an = c1.selectbox("Année", annees, index=len(annees)-1)
    pf = c2.selectbox("Plateforme", ["Toutes"] + plats_dyn, index=0)
    mo = c3.selectbox("Mois", ["Tous"] + [f"{i:02d}" for i in range(1,13)], index=0)

    data = df[df["AAAA"] == int(an)].copy()
    if app != "Tous":
        data = data[data["appartement"] == app]
    if pf != "Toutes":
        data = data[data["plateforme"].str.lower() == pf.lower()]
    if mo != "Tous":
        data = data[data["MM"] == int(mo)]
    if data.empty:
        st.info("Aucune donnée pour ces filtres.")
        return

    # Détail (avec noms)
    detail = data.copy()
    for c in ["date_arrivee","date_depart"]:
        detail[c] = detail[c].apply(format_date_str)
    by = [c for c in ["appartement","date_arrivee","nom_client"] if c in detail.columns]
    detail = detail.sort_values(by=by, na_position="last").reset_index(drop=True)
    cols = ["appartement","nom_client","plateforme","telephone","date_arrivee","date_depart","nuitees","prix_brut","prix_net","charges","%","sms_status"]
    cols = [c for c in cols if c in detail.columns]
    st.dataframe(detail[cols], use_container_width=True)

    # Totaux
    total_brut   = data["prix_brut"].sum(skipna=True)
    total_net    = data["prix_net"].sum(skipna=True)
    total_chg    = data["charges"].sum(skipna=True)
    total_nuits  = data["nuitees"].sum(skipna=True)
    pct_moy = (data["charges"].sum() / data["prix_brut"].sum() * 100) if data["prix_brut"].sum() else 0
    st.markdown(totaux_html(total_brut, total_net, total_chg, total_nuits, pct_moy), unsafe_allow_html=True)

    # Agrégats MM x plateforme (on enlève les lignes 0)
    stats = (
        data.groupby(["MM","plateforme"], dropna=True)
            .agg(prix_brut=("prix_brut","sum"),
                 prix_net=("prix_net","sum"),
                 charges=("charges","sum"),
                 nuitees=("nuitees","sum"))
            .reset_index()
    )
    stats = stats[(stats["prix_brut"]!=0) | (stats["prix_net"]!=0) | (stats["charges"]!=0) | (stats["nuitees"]!=0)]
    stats = stats.sort_values(["MM","plateforme"]).reset_index(drop=True)
    if stats.empty:
        st.info("Aucun agrégat non nul.")
        return

    cmap_fn = build_platform_cmap(df_pal)
    plats_u = sorted(stats["plateforme"].unique().tolist())

    def plot_grouped_bars(metric: str, title: str, ylabel: str):
        months = list(range(1,13))
        base_x = np.arange(len(months), dtype=float)
        width = 0.8 / max(1,len(plats_u))

        fig, ax = plt.subplots(figsize=(10,4))
        for i, p in enumerate(plats_u):
            sub = stats[stats["plateforme"] == p]
            vals = {int(mm): float(v) for mm, v in zip(sub["MM"], sub[metric])}
            y = np.array([vals.get(m, 0.0) for m in months], dtype=float)
            x = base_x + (i - (len(plats_u)-1)/2) * width
            ax.bar(x, y, width=width, label=p, color=cmap_fn(p))

        ax.set_xlim(-0.5, 11.5)
        ax.set_xticks(base_x)
        ax.set_xticklabels([f"{m:02d}" for m in months])
        ax.set_xlabel(f"Mois ({an})")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.legend(loc="upper left", frameon=False)
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        st.pyplot(fig)
        plt.close(fig)

    plot_grouped_bars("prix_brut", "💰 Revenus bruts", "€")
    plot_grouped_bars("prix_net",  "🏦 Revenus nets",  "€")
    plot_grouped_bars("nuitees",   "🛌 Nuitées",       "Nuitées")

    # Export détail XLSX
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        detail[cols].to_excel(w, index=False)
    st.download_button(
        "⬇️ Télécharger le détail (XLSX)",
        data=buf.getvalue(),
        file_name=f"rapport_detail_{an}{'' if mo=='Tous' else '_'+mo}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

def vue_clients(df: pd.DataFrame):
    st.title("👥 Liste des clients")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    c0, c1, c2 = st.columns(3)
    apps = ["Tous"] + sorted(df["appartement"].dropna().astype(str).unique().tolist())
    app = c0.selectbox("Appartement", apps, index=0)
    annees = ["Toutes"] + sorted(df["AAAA"].dropna().astype(int).unique().tolist())
    an = c1.selectbox("Année", annees, index=0 if len(annees)==1 else len(annees)-1)
    mo = c2.selectbox("Mois", ["Tous"] + [f"{i:02d}" for i in range(1,13)], index=0)

    data = df.copy()
    if app != "Tous":
        data = data[data["appartement"] == app]
    if an != "Toutes":
        data = data[data["AAAA"] == int(an)]
    if mo != "Tous":
        data = data[data["MM"] == int(mo)]
    if data.empty:
        st.info("Aucune donnée pour cette période.")
        return

    data["prix_brut/nuit"] = data.apply(lambda r: round((r["prix_brut"]/r["nuitees"]) if r["nuitees"] else 0,2), axis=1)
    data["prix_net/nuit"]  = data.apply(lambda r: round((r["prix_net"]/r["nuitees"])  if r["nuitees"] else 0,2), axis=1)

    show = data.copy()
    for c in ["date_arrivee","date_depart"]:
        show[c] = show[c].apply(format_date_str)
    cols = ["appartement","nom_client","plateforme","telephone","date_arrivee","date_depart",
            "nuitees","prix_brut","prix_net","charges","%","prix_brut/nuit","prix_net/nuit","sms_status"]
    cols = [c for c in cols if c in show.columns]
    st.dataframe(show[cols], use_container_width=True)

    st.download_button(
        "📥 Télécharger (CSV)",
        data=show[cols].to_csv(index=False).encode("utf-8"),
        file_name="clients.csv",
        mime="text/csv"
    )

# ==============================  SMS (MANUEL) ==============================

def sms_message_arrivee(row: pd.Series) -> str:
    d1 = row.get("date_arrivee"); d2 = row.get("date_depart")
    d1s = d1.strftime("%Y/%m/%d") if isinstance(d1, date) else ""
    d2s = d2.strftime("%Y/%m/%d") if isinstance(d2, date) else ""
    nuitees = int(row.get("nuitees") or ((d2 - d1).days if isinstance(d1, date) and isinstance(d2, date) else 0))
    app = str(row.get("appartement") or "")
    pf = str(row.get("plateforme") or "")
    nom = str(row.get("nom_client") or "")
    tel_aff = str(row.get("telephone") or "").strip()
    return (
        f"{app}\n"
        f"Plateforme : {pf}\n"
        f"Date d'arrivee : {d1s}  Date depart : {d2s}  Nombre de nuitees : {nuitees}\n\n"
        f"Bonjour {nom}\n"
        f"Telephone : {tel_aff}\n\n"
        "Bienvenue chez nous !\n\n "
        "Nous sommes ravis de vous accueillir bientot. Pour organiser au mieux votre reception, pourriez-vous nous indiquer "
        "a quelle heure vous pensez arriver.\n\n "
        "Sachez egalement qu'une place de parking est a votre disposition dans l'immeuble, en cas de besoin.\n\n "
        "Nous vous souhaitons un excellent voyage et nous nous rejouissons de vous rencontrer.\n\n "
        "Annick & Charley"
    )

def sms_message_depart(row: pd.Series) -> str:
    nom = str(row.get("nom_client") or "")
    return (
        f"Bonjour {nom},\n\n"
        "Un grand merci d’avoir choisi notre appartement pour votre séjour ! "
        "Nous espérons que vous avez passé un moment aussi agréable que celui que nous avons eu à vous accueillir.\n\n"
        "Si l’envie vous prend de revenir explorer encore un peu notre ville (ou simplement retrouver le confort de notre petit cocon), "
        "sachez que notre porte vous sera toujours grande ouverte.\n\n"
        "Au plaisir de vous accueillir à nouveau,\n"
        "Annick & Charley"
    )

def vue_sms(df: pd.DataFrame):
    st.title("✉️ SMS (envoi manuel)")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    today = date.today()
    demain = today + timedelta(days=1)
    hier = today - timedelta(days=1)

    # Filtre appartement
    apps = ["Tous"] + sorted(df["appartement"].dropna().astype(str).unique().tolist())
    app = st.selectbox("Appartement", apps, index=0)

    data = df.copy()
    if app != "Tous":
        data = data[data["appartement"] == app]

    # Arrivées demain
    st.subheader("📆 Arrivées demain")
    arrives = data[data["date_arrivee"] == demain].copy()
    if arrives.empty:
        st.info("Aucune arrivée demain.")
    else:
        for idx, r in arrives.reset_index(drop=True).iterrows():
            body = sms_message_arrivee(r)
            tel = normalize_tel(r.get("telephone"))
            tel_link = f"tel:{tel}" if tel else ""
            sms_link = f"sms:{tel}?&body={quote(body)}" if tel and body else ""

            st.markdown(f"**{r.get('nom_client','')}** — {r.get('plateforme','')} — {r.get('appartement','')}")
            st.markdown(f"Arrivée: {format_date_str(r.get('date_arrivee'))} • "
                        f"Départ: {format_date_str(r.get('date_depart'))} • "
                        f"Nuitées: {r.get('nuitees','')}")
            st.code(body)
            c1, c2, c3 = st.columns([1,1,2])
            call = c1.checkbox("📞 Appeler", key=f"sms_arr_call_{idx}", value=False)
            send = c2.checkbox("📩 SMS", key=f"sms_arr_sms_{idx}", value=True)
            with c3:
                if call and tel_link:
                    st.link_button(f"Appeler {tel}", tel_link)
                if send and sms_link:
                    st.link_button("Envoyer SMS", sms_link)
            st.divider()

    # Relance +24h après départ
    st.subheader("🕒 Relance +24h après départ")
    dep_24h = data[data["date_depart"] == hier].copy()
    if dep_24h.empty:
        st.info("Aucun départ hier.")
    else:
        for idx, r in dep_24h.reset_index(drop=True).iterrows():
            body = sms_message_depart(r)
            tel = normalize_tel(r.get("telephone"))
            tel_link = f"tel:{tel}" if tel else ""
            sms_link = f"sms:{tel}?&body={quote(body)}" if tel and body else ""

            st.markdown(f"**{r.get('nom_client','')}** — {r.get('plateforme','')} — {r.get('appartement','')}")
            st.code(body)
            c1, c2, c3 = st.columns([1,1,2])
            call = c1.checkbox("📞 Appeler", key=f"sms_dep_call_{idx}", value=False)
            send = c2.checkbox("📩 SMS", key=f"sms_dep_sms_{idx}", value=True)
            with c3:
                if call and tel_link:
                    st.link_button(f"Appeler {tel}", tel_link)
                if send and sms_link:
                    st.link_button("Envoyer SMS", sms_link)
            st.divider()

# ==============================  EXPORT ICS VIEW  ==========================

def vue_export_ics(df: pd.DataFrame):
    st.title("📤 Export ICS (Google Agenda – Import manuel)")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée à exporter.")
        return

    c0, c1, c2, c3 = st.columns(4)
    apps = ["Tous"] + sorted(df["appartement"].dropna().astype(str).unique().tolist())
    app = c0.selectbox("Appartement", apps, index=0)
    annees = ["Toutes"] + sorted(df["AAAA"].dropna().astype(int).unique().tolist())
    an = c1.selectbox("Année", annees, index=len(annees)-1 if len(annees)>1 else 0)
    df_pal = load_platform_palette()
    plats_dyn = platform_options(df, df_pal)
    pf = c2.selectbox("Plateforme", ["Toutes"] + plats_dyn, index=0)
    mo = c3.selectbox("Mois", ["Tous"] + [f"{i:02d}" for i in range(1,13)], index=0)

    data = df.copy()
    if app != "Tous":
        data = data[data["appartement"] == app]
    if an != "Toutes":
        data = data[data["AAAA"] == int(an)]
    if pf != "Toutes":
        data = data[data["plateforme"].str.lower() == pf.lower()]
    if mo != "Tous":
        data = data[data["MM"] == int(mo)]

    if data.empty:
        st.info("Aucune réservation pour ces filtres.")
        return

    ics_text = df_to_ics(data)
    st.download_button(
        "⬇️ Télécharger reservations_multi.ics",
        data=ics_text.encode("utf-8"),
        file_name="reservations_multi.ics",
        mime="text/calendar"
    )
    st.caption("Google Agenda → Paramètres → Importer & exporter → Importer → sélectionnez ce .ics.")

# ==============================  APP  ======================================

def main():
    st.set_page_config(page_title="📚 Réservations (Multi)", layout="wide")

    # Sidebar : Fichier
    st.sidebar.title("📁 Fichier")
    df_tmp = charger_donnees()
    bouton_telecharger(df_tmp)
    bouton_restaurer()

    # Sidebar : Navigation
    st.sidebar.title("🧭 Navigation")
    onglet = st.sidebar.radio(
        "Aller à",
        ["📋 Réservations","➕ Ajouter","✏️ Modifier / Supprimer",
         "📅 Calendrier","📊 Rapport","👥 Liste clients","✉️ SMS","📤 Export ICS"]
    )

    # Sidebar : Palette plateformes & Maintenance
    sidebar_platform_manager()
    render_cache_section_sidebar()

    # Charger données (après éventuelle restauration)
    df = charger_donnees()

    if onglet == "📋 Réservations":
        vue_reservations(df)
    elif onglet == "➕ Ajouter":
        vue_ajouter(df)
    elif onglet == "✏️ Modifier / Supprimer":
        vue_modifier(df)
    elif onglet == "📅 Calendrier":
        vue_calendrier(df)
    elif onglet == "📊 Rapport":
        vue_rapport(df)
    elif onglet == "👥 Liste clients":
        vue_clients(df)
    elif onglet == "✉️ SMS":
        vue_sms(df)
    elif onglet == "📤 Export ICS":
        vue_export_ics(df)

if __name__ == "__main__":
    main()