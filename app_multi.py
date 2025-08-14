# app_multi.py — Réservations Multi (PARTIE 1/3)
# Calculs demandés :
#   montant_brut = montant_net - commissions - frais_cb
#   base         = montant_brut - menage - taxes_sejour
#   %            = (montant_brut - montant_net) / montant_brut * 100   (si montant_brut > 0, sinon 0)

import streamlit as st
import pandas as pd
import numpy as np
import calendar
from datetime import date, timedelta
from io import BytesIO
import os

FICHIER = "reservations_multi.xlsx"

# ----------------------------- UTILITAIRES -----------------------------

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
    """Force le téléphone en texte, retire espaces et .0 éventuels."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = str(x).strip().replace(" ", "")
    if s.endswith(".0"):
        s = s[:-2]
    return s

def compute_finance_fields(row: pd.Series) -> pd.Series:
    """
    Applique les formules Multi:
      montant_brut = montant_net - commissions - frais_cb
      base         = montant_brut - menage - taxes_sejour
      %            = (montant_brut - montant_net) / montant_brut * 100
    """
    net   = float(row.get("montant_net") or 0)
    com   = float(row.get("commissions") or 0)
    fcb   = float(row.get("frais_cb") or 0)
    men   = float(row.get("menage") or 0)
    taxe  = float(row.get("taxes_sejour") or 0)

    brut  = net - com - fcb
    base  = brut - men - taxe
    pct   = ((brut - net) / brut * 100) if brut else 0.0

    row["montant_brut"] = round(brut, 2)
    row["base"]         = round(base, 2)
    row["%"]            = round(pct, 2)
    return row

def is_total_row(row: pd.Series) -> bool:
    name_is_total = str(row.get("nom_client","")).strip().lower() == "total"
    pf_is_total   = str(row.get("plateforme","")).strip().lower() == "total"
    apt_is_total  = str(row.get("appartement","")).strip().lower() == "total"
    d1 = row.get("date_arrivee"); d2 = row.get("date_depart")
    no_dates = not isinstance(d1, date) and not isinstance(d2, date)
    # une ligne "total" sans dates mais avec des montants ≠ 0
    has_money = any(pd.notna(row.get(c)) and float(row.get(c) or 0) != 0 for c in [
        "montant_net","commissions","frais_cb","montant_brut","menage","taxes_sejour","base"
    ])
    return name_is_total or pf_is_total or apt_is_total or (no_dates and has_money)

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

# ----------------------------- SCHEMA / NORMALISATION -----------------------------

BASE_COLS = [
    "appartement",
    "nom_client",
    "plateforme",
    "telephone",
    "date_arrivee",
    "date_depart",
    "nuitees",

    "montant_net",
    "commissions",
    "frais_cb",
    "montant_brut",   # calculé
    "menage",
    "taxes_sejour",
    "base",           # calculé
    "%",              # calculé

    "AAAA",
    "MM",
    "ical_uid",
    "sms_statut",     # 🟠 attente / 🟢 envoyé
]

def ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Force la présence des colonnes, types cohérents, calcule brut/base/% et AAAA/MM."""
    if df is None or df.empty:
        out = pd.DataFrame(columns=BASE_COLS)
        return out

    df = df.copy()

    # Dates -> date
    for c in ["date_arrivee", "date_depart"]:
        if c in df.columns:
            df[c] = df[c].apply(to_date_only)

    # Téléphone en texte normalisé
    if "telephone" in df.columns:
        df["telephone"] = df["telephone"].apply(normalize_tel)

    # Numériques (saisissables)
    for c in ["montant_net","commissions","frais_cb","menage","taxes_sejour"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    # Colonnes manquantes par défaut
    defaults = {
        "appartement": "",
        "nom_client": "",
        "plateforme": "Autre",
        "telephone": "",
        "montant_brut": 0.0,
        "base": 0.0,
        "%": 0.0,
        "AAAA": pd.NA,
        "MM": pd.NA,
        "ical_uid": "",
        "sms_statut": "🟠",
    }
    for k, v in defaults.items():
        if k not in df.columns:
            df[k] = v

    # Nuitées
    if "date_arrivee" in df.columns and "date_depart" in df.columns:
        df["nuitees"] = [
            (d2 - d1).days if (isinstance(d1, date) and isinstance(d2, date)) else np.nan
            for d1, d2 in zip(df["date_arrivee"], df["date_depart"])
        ]

    # AAAA / MM
    if "date_arrivee" in df.columns:
        df["AAAA"] = df["date_arrivee"].apply(lambda d: d.year if isinstance(d, date) else pd.NA).astype("Int64")
        df["MM"]   = df["date_arrivee"].apply(lambda d: d.month if isinstance(d, date) else pd.NA).astype("Int64")

    # Calculs financiers (ligne à ligne)
    df = df.apply(compute_finance_fields, axis=1)

    # Ordre colonnes
    out = df[[c for c in BASE_COLS if c in df.columns] + [c for c in df.columns if c not in BASE_COLS]]
    return out

# ----------------------------- EXCEL I/O -----------------------------

@st.cache_data(show_spinner=False)
def _read_excel_cached(path: str, mtime: float):
    # IMPORTANT : forcer le téléphone en texte à la lecture
    return pd.read_excel(path, converters={"telephone": normalize_tel})

def charger_donnees() -> pd.DataFrame:
    if not os.path.exists(FICHIER):
        # Crée un squelette vide si le fichier n'existe pas
        return ensure_schema(pd.DataFrame())
    try:
        mtime = os.path.getmtime(FICHIER)
        df = _read_excel_cached(FICHIER, mtime)
        return ensure_schema(df)
    except Exception as e:
        st.error(f"Erreur de lecture Excel : {e}")
        return ensure_schema(pd.DataFrame())

def _force_telephone_text_format_openpyxl(writer, df_to_save: pd.DataFrame, sheet_name: str):
    """Après to_excel, force le format texte '@' sur la colonne 'telephone' si présente."""
    try:
        ws = writer.sheets.get(sheet_name) or writer.sheets.get("Sheet1")
        if ws is None:
            return
        if "telephone" not in df_to_save.columns:
            return
        col_idx = df_to_save.columns.get_loc("telephone") + 1  # 1-based
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=col_idx, max_col=col_idx):
            row[0].number_format = "@"
    except Exception:
        pass  # ignore styling errors

def sauvegarder_donnees(df: pd.DataFrame):
    df = ensure_schema(df)
    core, totals = split_totals(df)
    core = sort_core(core)
    out = pd.concat([core, totals], ignore_index=True)

    try:
        with pd.ExcelWriter(FICHIER, engine="openpyxl") as w:
            out.to_excel(w, index=False, sheet_name="Réservations")
            _force_telephone_text_format_openpyxl(w, out, "Réservations")
        st.cache_data.clear()
        st.success("💾 Sauvegarde Excel effectuée.")
    except Exception as e:
        st.error(f"Échec de sauvegarde Excel : {e}")

def bouton_restaurer():
    up = st.sidebar.file_uploader("📤 Restaurer xlsx (multi)", type=["xlsx"], help="Remplace le fichier actuel")
    if up is not None:
        try:
            df_new = pd.read_excel(up, converters={"telephone": normalize_tel})
            df_new = ensure_schema(df_new)
            sauvegarder_donnees(df_new)
            st.sidebar.success("✅ Fichier restauré.")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Erreur import : {e}")

def bouton_telecharger(df: pd.DataFrame):
    buf = BytesIO()
    try:
        ensure_schema(df).to_excel(buf, index=False, engine="openpyxl", sheet_name="Réservations")
        data_xlsx = buf.getvalue()
    except Exception as e:
        st.sidebar.error(f"Export XLSX indisponible : {e}")
        data_xlsx = None

    st.sidebar.download_button(
        "💾 Sauvegarde xlsx",
        data=data_xlsx if data_xlsx else b"",
        file_name="reservations_multi.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=(data_xlsx is None),
    )
# ==============================  PALETTE / COULEURS PF ==============================

DEFAULT_PF_COLORS = {
    "Booking": "🟦",
    "Airbnb": "🟩",
    "Autre": "🟧",
}

def get_pf_colors(df: pd.DataFrame) -> dict:
    if "pf_colors" not in st.session_state:
        st.session_state.pf_colors = DEFAULT_PF_COLORS.copy()
    # S'assure que toutes les PF existantes ont une couleur
    for pf in sorted(df.get("plateforme", pd.Series()).dropna().unique().tolist()):
        if pf not in st.session_state.pf_colors:
            st.session_state.pf_colors[pf] = "⬜"
    return st.session_state.pf_colors

def ui_manage_platform_colors(df: pd.DataFrame):
    with st.expander("🎨 Plateformes & couleurs (session)", expanded=False):
        pf_colors = get_pf_colors(df)
        # Ajout d'une nouvelle PF
        c1, c2, c3 = st.columns([2,1,1])
        new_pf = c1.text_input("Ajouter une plateforme", value="", placeholder="ex : VRBO")
        new_color = c2.selectbox("Couleur", ["🟥","🟧","🟨","🟩","🟦","🟪","⬛","⬜"], index=4, key="new_pf_color")
        if c3.button("Ajouter PF"):
            if new_pf.strip():
                pf_colors[new_pf.strip()] = new_color
                st.success(f"Plateforme '{new_pf.strip()}' ajoutée.")
        # Table simple d’édition
        for pf in sorted(pf_colors.keys()):
            colA, colB, colC = st.columns([2,1,1])
            colA.markdown(f"**{pf}**")
            cur = pf_colors[pf]
            sel = colB.selectbox(f"Couleur {pf}", ["🟥","🟧","🟨","🟩","🟦","🟪","⬛","⬜"], index=["🟥","🟧","🟨","🟩","🟦","🟪","⬛","⬜"].index(cur), key=f"pfcol_{pf}")
            pf_colors[pf] = sel
            if colC.button("Retirer", key=f"pfdel_{pf}"):
                if pf not in DEFAULT_PF_COLORS:
                    del pf_colors[pf]
                    st.experimental_rerun()

# ==============================  WIDGETS / BRIQUES UI ==============================

def chips_totaux_multi(df: pd.DataFrame):
    if df.empty:
        return
    total_net  = float(df["montant_net"].sum(skipna=True))
    total_com  = float(df["commissions"].sum(skipna=True))
    total_fcb  = float(df["frais_cb"].sum(skipna=True))
    total_brut = float(df["montant_brut"].sum(skipna=True))
    total_men  = float(df["menage"].sum(skipna=True))
    total_tax  = float(df["taxes_sejour"].sum(skipna=True))
    total_base = float(df["base"].sum(skipna=True))
    total_nuit = float(df["nuitees"].sum(skipna=True))
    pct_moy    = ((total_brut - total_net) / total_brut * 100) if total_brut else 0.0

    st.markdown(f"""
<style>
.chips {{display:flex; flex-wrap:wrap; gap:10px; margin:.5rem 0 1rem 0}}
.chip  {{padding:8px 12px; border:1px solid rgba(127,127,127,.25); border-radius:10px; background:rgba(127,127,127,.08)}}
.chip b{{display:block; font-size:.85rem; margin-bottom:.15rem}}
</style>
<div class="chips">
  <div class="chip"><b>Total Net</b><div>{total_net:,.2f} €</div></div>
  <div class="chip"><b>Commissions</b><div>{total_com:,.2f} €</div></div>
  <div class="chip"><b>Frais CB</b><div>{total_fcb:,.2f} €</div></div>
  <div class="chip"><b>Montant brut</b><div>{total_brut:,.2f} €</div></div>
  <div class="chip"><b>Ménage</b><div>{total_men:,.2f} €</div></div>
  <div class="chip"><b>Taxes séjour</b><div>{total_tax:,.2f} €</div></div>
  <div class="chip"><b>Base</b><div>{total_base:,.2f} €</div></div>
  <div class="chip"><b>Nuitées</b><div>{int(total_nuit) if pd.notna(total_nuit) else 0}</div></div>
  <div class="chip"><b>Commission moy.</b><div>{pct_moy:.2f} %</div></div>
</div>
""", unsafe_allow_html=True)

def inline(label, widget_fn, key=None, **kwargs):
    c1, c2 = st.columns([1,2])
    with c1: st.markdown(f"**{label}**")
    with c2: return widget_fn(label, key=key, label_visibility="collapsed", **kwargs)

# ==============================  VUE : RÉSERVATIONS ==============================

def vue_reservations(df: pd.DataFrame):
    st.header("📋 Réservations (Multi)")
    if df.empty:
        st.info("Aucune réservation.")
        return

    # Filtres
    colf = st.columns(4)
    apt_opt = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    pf_opt  = ["Toutes"] + sorted(df["plateforme"].dropna().unique().tolist())
    an_opt  = ["Toutes"] + sorted([int(x) for x in df["AAAA"].dropna().unique().tolist()])
    mois_opt= ["Tous"] + [f"{i:02d}" for i in range(1,13)]

    apt  = colf[0].selectbox("Appartement", apt_opt)
    pf   = colf[1].selectbox("Plateforme", pf_opt)
    an   = colf[2].selectbox("Année", an_opt)
    mois = colf[3].selectbox("Mois", mois_opt)

    data = df.copy()
    if apt  != "Tous":   data = data[data["appartement"] == apt]
    if pf   != "Toutes": data = data[data["plateforme"] == pf]
    if an   != "Toutes": data = data[data["AAAA"] == int(an)]
    if mois != "Tous":   data = data[data["MM"] == int(mois)]

    chips_totaux_multi(data)

    # Affichage formaté
    show = data.copy()
    for c in ["date_arrivee","date_depart"]:
        show[c] = show[c].apply(format_date_str)
    cols = ["appartement","nom_client","plateforme","telephone",
            "date_arrivee","date_depart","nuitees",
            "montant_net","commissions","frais_cb","montant_brut",
            "menage","taxes_sejour","base","%","sms_statut"]
    cols = [c for c in cols if c in show.columns]
    st.dataframe(show[cols], use_container_width=True)

    # Gestion couleurs PF (session)
    ui_manage_platform_colors(df)

# ==============================  VUE : AJOUTER ==============================

def vue_ajouter(df: pd.DataFrame):
    st.header("➕ Ajouter une réservation")

    with st.form("add_multi"):
        apt  = inline("Appartement", st.text_input, key="add_apt", value="")
        nom  = inline("Nom client", st.text_input, key="add_nom", value="")
        tel  = inline("Téléphone (+33...)", st.text_input, key="add_tel", value="")
        # Plateforme = combo sur PF existantes + champ ajout rapide
        pf_colors = get_pf_colors(df)
        pf_list = sorted(pf_colors.keys())
        pf_sel = inline("Plateforme", st.selectbox, key="add_pf", options=pf_list, index=pf_list.index("Autre") if "Autre" in pf_list else 0)
        pf_new = inline("Nouvelle PF (optionnel)", st.text_input, key="add_pf_new", value="")
        if pf_new.strip():
            pf_sel = pf_new.strip()
            if pf_sel not in pf_colors:
                pf_colors[pf_sel] = "⬜"

        arr  = inline("Arrivée", st.date_input, key="add_arr", value=date.today())
        dep  = inline("Départ",  st.date_input, key="add_dep", value=date.today()+timedelta(days=1), min_value=date.today()+timedata(days=1) if False else arr+timedelta(days=1))

        # Finances (saisies)
        net  = inline("Montant net (€)", st.number_input, key="add_net", min_value=0.0, step=1.0, format="%.2f")
        com  = inline("Commissions (€)", st.number_input, key="add_com", min_value=0.0, step=1.0, format="%.2f")
        fcb  = inline("Frais CB (€)",    st.number_input, key="add_fcb", min_value=0.0, step=1.0, format="%.2f")
        men  = inline("Ménage (€)",      st.number_input, key="add_men", min_value=0.0, step=1.0, format="%.2f")
        tax  = inline("Taxes séjour (€)",st.number_input, key="add_tax", min_value=0.0, step=1.0, format="%.2f")

        # Calculs live
        brut = float(net) - float(com) - float(fcb)
        base = brut - float(men) - float(tax)
        pct  = ((brut - float(net)) / brut * 100) if brut else 0.0

        inline("Montant brut (€)", st.number_input, key="add_brut", value=round(brut,2), step=0.01, format="%.2f", disabled=True)
        inline("Base (€)",          st.number_input, key="add_base", value=round(base,2), step=0.01, format="%.2f", disabled=True)
        inline("Commission (%)",    st.number_input, key="add_pct",  value=round(pct,2),  step=0.01, format="%.2f", disabled=True)

        ok = st.form_submit_button("Enregistrer")

    if ok:
        if not apt.strip():
            st.error("Appartement requis.")
            return
        if dep < arr + timedelta(days=1):
            st.error("La date de départ doit être au moins le lendemain de l’arrivée.")
            return

        row = {
            "appartement": apt.strip(),
            "nom_client": (nom or "").strip(),
            "plateforme": pf_sel,
            "telephone": normalize_tel(tel),
            "date_arrivee": arr,
            "date_depart": dep,
            "nuitees": (dep - arr).days,
            "montant_net": float(net),
            "commissions": float(com),
            "frais_cb": float(fcb),
            "montant_brut": 0.0,   # calculé ensuite
            "menage": float(men),
            "taxes_sejour": float(tax),
            "base": 0.0,           # calculé ensuite
            "%": 0.0,              # calculé ensuite
            "AAAA": arr.year,
            "MM": arr.month,
            "ical_uid": "",
            "sms_statut": "🟠",     # en attente par défaut
        }
        row = compute_finance_fields(pd.Series(row))
        df2 = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        sauvegarder_donnees(df2)
        st.success("✅ Réservation ajoutée.")
        st.experimental_rerun()

# ==============================  VUE : MODIFIER / SUPPRIMER ==============================

def vue_modifier(df: pd.DataFrame):
    st.header("✏️ Modifier / Supprimer")
    if df.empty:
        st.info("Aucune réservation.")
        return

    df = df.copy()
    df["id_aff"] = df["appartement"].astype(str) + " | " + df["nom_client"].astype(str) + " | " + df["date_arrivee"].apply(format_date_str)
    choix = st.selectbox("Choisir une réservation", df["id_aff"])

    row = df[df["id_aff"] == choix]
    if row.empty:
        st.warning("Sélection invalide.")
        return
    i = row.index[0]

    c0, c1 = st.columns([1,1])
    apt = c0.text_input("Appartement", df.at[i, "appartement"])
    nom = c1.text_input("Nom client", df.at[i, "nom_client"])
    tel = st.text_input("Téléphone (+33...)", df.at[i, "telephone"])

    # Plateforme
    pf_colors = get_pf_colors(df)
    pf_list = sorted(pf_colors.keys() | set([df.at[i,"plateforme"]]))
    pf_sel = st.selectbox("Plateforme", pf_list, index=pf_list.index(df.at[i,"plateforme"]))
    pf_new = st.text_input("Nouvelle PF (optionnel)", value="")
    if pf_new.strip():
        pf_sel = pf_new.strip()
        if pf_sel not in pf_colors:
            pf_colors[pf_sel] = "⬜"

    arr = st.date_input("Arrivée", df.at[i, "date_arrivee"] if isinstance(df.at[i,"date_arrivee"], date) else date.today())
    dep = st.date_input("Départ",  df.at[i, "date_depart"] if isinstance(df.at[i,"date_depart"], date) else arr+timedelta(days=1), min_value=arr+timedelta(days=1))

    c2, c3, c4 = st.columns(3)
    net = c2.number_input("Montant net (€)", min_value=0.0, value=float(df.at[i,"montant_net"]) if pd.notna(df.at[i,"montant_net"]) else 0.0, step=1.0, format="%.2f")
    com = c3.number_input("Commissions (€)", min_value=0.0, value=float(df.at[i,"commissions"]) if pd.notna(df.at[i,"commissions"]) else 0.0, step=1.0, format="%.2f")
    fcb = c4.number_input("Frais CB (€)",    min_value=0.0, value=float(df.at[i,"frais_cb"]) if pd.notna(df.at[i,"frais_cb"]) else 0.0, step=1.0, format="%.2f")

    c5, c6 = st.columns(2)
    men = c5.number_input("Ménage (€)",      min_value=0.0, value=float(df.at[i,"menage"]) if pd.notna(df.at[i,"menage"]) else 0.0, step=1.0, format="%.2f")
    tax = c6.number_input("Taxes séjour (€)",min_value=0.0, value=float(df.at[i,"taxes_sejour"]) if pd.notna(df.at[i,"taxes_sejour"]) else 0.0, step=1.0, format="%.2f")

    # Calculs live
    brut = float(net) - float(com) - float(fcb)
    base = brut - float(men) - float(tax)
    pct  = ((brut - float(net)) / brut * 100) if brut else 0.0
    st.markdown(f"**Montant brut** : {brut:.2f} € — **Base** : {base:.2f} € — **%** : {pct:.2f} %")

    c7, c8 = st.columns(2)
    if c7.button("💾 Enregistrer"):
        if dep < arr + timedelta(days=1):
            st.error("Départ au minimum le lendemain de l’arrivée.")
            return
        df.at[i, "appartement"] = apt.strip()
        df.at[i, "nom_client"]  = nom.strip()
        df.at[i, "telephone"]   = normalize_tel(tel)
        df.at[i, "plateforme"]  = pf_sel
        df.at[i, "date_arrivee"]= arr
        df.at[i, "date_depart"] = dep
        df.at[i, "nuitees"]     = (dep - arr).days
        df.at[i, "montant_net"] = float(net)
        df.at[i, "commissions"] = float(com)
        df.at[i, "frais_cb"]    = float(fcb)
        df.at[i, "menage"]      = float(men)
        df.at[i, "taxes_sejour"]= float(tax)
        df.at[i, "AAAA"]        = arr.year
        df.at[i, "MM"]          = arr.month
        # Re-calculs
        df.iloc[i] = compute_finance_fields(df.iloc[i])
        df.drop(columns=["id_aff"], inplace=True, errors="ignore")
        sauvegarder_donnees(df)
        st.success("✅ Modifié.")
        st.experimental_rerun()

    if c8.button("🗑 Supprimer"):
        df2 = df.drop(index=i)
        df2.drop(columns=["id_aff"], inplace=True, errors="ignore")
        sauvegarder_donnees(df2)
        st.warning("Supprimé.")
        st.experimental_rerun()

# ==============================  VUE : CALENDRIER ==============================

def vue_calendrier(df: pd.DataFrame):
    st.header("📅 Calendrier mensuel")
    if df.empty:
        st.info("Aucune donnée.")
        return

    cols = st.columns(3)
    apt_opt = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    apt = cols[0].selectbox("Appartement", apt_opt)
    mois_nom = cols[1].selectbox("Mois", list(calendar.month_name)[1:], index=max(0, date.today().month-1))
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    if not annees:
        st.warning("Aucune année disponible.")
        return
    annee = cols[2].selectbox("Année", annees, index=len(annees)-1)

    data = df.copy()
    if apt != "Tous":
        data = data[data["appartement"] == apt]

    mois_index = list(calendar.month_name).index(mois_nom)
    jours = [date(annee, mois_index, j+1) for j in range(calendar.monthrange(annee, mois_index)[1])]
    planning = {j: [] for j in jours}
    pf_colors = get_pf_colors(df)

    core = data.copy()
    for _, r in core.iterrows():
        d1, d2 = r.get("date_arrivee"), r.get("date_depart")
        if not (isinstance(d1, date) and isinstance(d2, date)):
            continue
        for j in jours:
            if d1 <= j < d2:
                ic = pf_colors.get(r.get("plateforme","Autre"), "⬜")
                planning[j].append(f"{ic} {r.get('nom_client','')}")

    table = []
    for semaine in calendar.monthcalendar(annee, mois_index):
        ligne = []
        for jour in semaine:
            if jour == 0:
                ligne.append("")
            else:
                d = date(annee, mois_index, jour)
                contenu = f"{jour}\n" + "\n".join(planning.get(d, []))
                ligne.append(contenu)
        table.append(ligne)
    st.table(pd.DataFrame(table, columns=["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"]))

# ==============================  VUE : RAPPORT ==============================

def vue_rapport(df: pd.DataFrame):
    st.header("📊 Rapport")
    if df.empty:
        st.info("Aucune donnée.")
        return

    colf = st.columns(4)
    apt_opt = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    pf_opt  = ["Toutes"] + sorted(df["plateforme"].dropna().unique().tolist())
    an_opt  = ["Toutes"] + sorted([int(x) for x in df["AAAA"].dropna().unique().tolist()])
    mois_opt= ["Tous"] + [f"{i:02d}" for i in range(1,13)]

    apt  = colf[0].selectbox("Appartement", apt_opt)
    pf   = colf[1].selectbox("Plateforme", pf_opt)
    an   = colf[2].selectbox("Année", an_opt)
    mois = colf[3].selectbox("Mois", mois_opt)

    data = df.copy()
    if apt  != "Tous":   data = data[data["appartement"] == apt]
    if pf   != "Toutes": data = data[data["plateforme"] == pf]
    if an   != "Toutes": data = data[data["AAAA"] == int(an)]
    if mois != "Tous":   data = data[data["MM"] == int(mois)]

    if data.empty:
        st.info("Aucune donnée pour ces filtres.")
        return

    # Détail (avec noms) trié
    detail = data.copy()
    for c in ["date_arrivee","date_depart"]:
        detail[c] = detail[c].apply(format_date_str)
    by = [c for c in ["appartement","date_arrivee","nom_client"] if c in detail.columns]
    detail = detail.sort_values(by=by, na_position="last").reset_index(drop=True)

    cols_detail = [
        "appartement","nom_client","plateforme","telephone",
        "date_arrivee","date_depart","nuitees",
        "montant_net","commissions","frais_cb","montant_brut",
        "menage","taxes_sejour","base","%"
    ]
    cols_detail = [c for c in cols_detail if c in detail.columns]
    st.dataframe(detail[cols_detail], use_container_width=True)

    # Totaux
    chips_totaux_multi(data)

    # Agrégations par mois & PF (pour graphes)
    stats = (
        data.groupby(["MM","plateforme"], dropna=True)
            .agg(montant_net=("montant_net","sum"),
                 montant_brut=("montant_brut","sum"),
                 base=("base","sum"),
                 nuitees=("nuitees","sum"))
            .reset_index()
    )
    if stats.empty:
        st.info("Aucune donnée agrégée.")
        return
    pivot_brut = stats.pivot(index="MM", columns="plateforme", values="montant_brut").fillna(0).sort_index()
    pivot_net  = stats.pivot(index="MM", columns="plateforme", values="montant_net").fillna(0).sort_index()
    pivot_base = stats.pivot(index="MM", columns="plateforme", values="base").fillna(0).sort_index()
    pivot_nuit = stats.pivot(index="MM", columns="plateforme", values="nuitees").fillna(0).sort_index()

    pivot_brut.index = [f"{int(m):02d}" for m in pivot_brut.index]
    pivot_net.index  = [f"{int(m):02d}" for m in pivot_net.index]
    pivot_base.index = [f"{int(m):02d}" for m in pivot_base.index]
    pivot_nuit.index = [f"{int(m):02d}" for m in pivot_nuit.index]

    st.markdown("**Montant brut par mois**")
    st.bar_chart(pivot_brut)
    st.markdown("**Montant net par mois**")
    st.bar_chart(pivot_net)
    st.markdown("**Base par mois**")
    st.bar_chart(pivot_base)
    st.markdown("**Nuitées par mois**")
    st.bar_chart(pivot_nuit)

# ==============================  VUE : SMS (manuel + journal) ==============================

def sms_message_arrivee_multi(r: pd.Series) -> str:
    d1 = r.get("date_arrivee"); d2 = r.get("date_depart")
    d1s = d1.strftime("%Y/%m/%d") if isinstance(d1, date) else ""
    d2s = d2.strftime("%Y/%m/%d") if isinstance(d2, date) else ""
    nuitees = int(r.get("nuitees") or ((d2-d1).days if isinstance(d1,date) and isinstance(d2,date) else 0))
    return (
        "VILLA TOBIAS\n"
        f"Appartement : {r.get('appartement','')}\n"
        f"Plateforme : {r.get('plateforme','')}\n"
        f"Date d'arrivee : {d1s}  Date depart : {d2s}  Nombre de nuitees : {nuitees}\n\n"
        f"Bonjour {r.get('nom_client','')}\n"
        f"Telephone : {r.get('telephone','')}\n\n"
        "Bienvenue chez nous !\n\n "
        "Nous sommes ravis de vous accueillir bientot. Pour organiser au mieux votre reception, pourriez-vous nous indiquer "
        "a quelle heure vous pensez arriver.\n\n "
        "Sachez egalement qu'une place de parking est a votre disposition dans l'immeuble, en cas de besoin.\n\n "
        "Nous vous souhaitons un excellent voyage et nous nous rejouissons de vous rencontrer.\n\n "
        "Annick & Charley"
    )

def sms_message_depart_multi(r: pd.Series) -> str:
    nom = str(r.get("nom_client") or "")
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
    st.header("✉️ SMS (manuel) + Journal")
    if df.empty:
        st.info("Aucune donnée.")
        return

    today = date.today()
    demain = today + timedelta(days=1)
    hier   = today - timedelta(days=1)

    # ARRIVÉES DEMAIN
    st.subheader("📆 Arrivées demain")
    arrs = df[df["date_arrivee"] == demain].copy()
    if arrs.empty:
        st.info("Aucune arrivée demain.")
    else:
        for idx, r in arrs.reset_index(drop=True).iterrows():
            body = sms_message_arrivee_multi(r)
            tel  = normalize_tel(r.get("telephone"))
            tel_link = f"tel:{tel}" if tel else ""
            sms_link = f"sms:{tel}?&body={quote(body)}" if tel else ""

            st.markdown(f"**{r.get('appartement','')} — {r.get('nom_client','')}** · {r.get('plateforme','')}")
            st.code(body)
            c1, c2, c3, c4 = st.columns([1,1,1,3])
            if tel_link: c1.link_button("📞 Appeler", tel_link)
            if sms_link: c2.link_button("📩 SMS", sms_link)
            mark_key = f"sms_mark_sent_arr_{idx}"
            if c3.button("🟢 Marquer envoyé", key=mark_key):
                # cherche la ligne réelle via clés
                mask = (
                    (df["appartement"] == r["appartement"]) &
                    (df["nom_client"]  == r["nom_client"]) &
                    (df["date_arrivee"]== r["date_arrivee"]) &
                    (df["date_depart"] == r["date_depart"])
                )
                df.loc[mask, "sms_statut"] = "🟢"
                sauvegarder_donnees(df)
                st.success("SMS marqué envoyé.")
                st.experimental_rerun()
            st.divider()

    # RELANCE APRES DEPART (hier)
    st.subheader("🕒 Relance +24h après départ")
    deps = df[df["date_depart"] == hier].copy()
    if deps.empty:
        st.info("Aucun départ hier.")
    else:
        for idx, r in deps.reset_index(drop=True).iterrows():
            body = sms_message_depart_multi(r)
            tel  = normalize_tel(r.get("telephone"))
            tel_link = f"tel:{tel}" if tel else ""
            sms_link = f"sms:{tel}?&body={quote(body)}" if tel else ""
            st.markdown(f"**{r.get('appartement','')} — {r.get('nom_client','')}** · {r.get('plateforme','')}")
            st.code(body)
            c1, c2, c3, c4 = st.columns([1,1,1,3])
            if tel_link: c1.link_button("📞 Appeler", tel_link)
            if sms_link: c2.link_button("📩 SMS", sms_link)
            mark_key = f"sms_mark_sent_dep_{idx}"
            if c3.button("🟢 Marquer envoyé", key=mark_key):
                mask = (
                    (df["appartement"] == r["appartement"]) &
                    (df["nom_client"]  == r["nom_client"]) &
                    (df["date_arrivee"]== r["date_arrivee"]) &
                    (df["date_depart"] == r["date_depart"])
                )
                df.loc[mask, "sms_statut"] = "🟢"
                sauvegarder_donnees(df)
                st.success("SMS marqué envoyé.")
                st.experimental_rerun()
            st.divider()

    # JOURNAL DES SMS
    st.subheader("📒 Journal des SMS (colonne 'sms_statut')")
    jcol = ["appartement","nom_client","plateforme","telephone","date_arrivee","date_depart","sms_statut"]
    jcol = [c for c in jcol if c in df.columns]
    show = df[jcol].copy()
    for c in ["date_arrivee","date_depart"]:
        if c in show.columns:
            show[c] = show[c].apply(format_date_str)
    st.dataframe(show, use_container_width=True)

# ==============================  VUE : EXPORT ICS ==============================

def _ics_escape(text: str) -> str:
    if text is None:
        return ""
    s = str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    return s

def _fmt_date_ics(d: date) -> str:
    return d.strftime("%Y%m%d")

def df_to_ics_multi(df: pd.DataFrame, cal_name: str = "Multi – Réservations") -> str:
    from datetime import timezone, datetime
    def _dtstamp():
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    df = ensure_schema(df)
    core, _ = split_totals(df)
    core = sort_core(core)

    lines = []
    A = lines.append
    A("BEGIN:VCALENDAR")
    A("VERSION:2.0")
    A("PRODID:-//Multi//Reservations//FR")
    A(f"X-WR-CALNAME:{_ics_escape(cal_name)}")
    A("CALSCALE:GREGORIAN")
    A("METHOD:PUBLISH")

    for _, r in core.iterrows():
        d1, d2 = r.get("date_arrivee"), r.get("date_depart")
        if not (isinstance(d1, date) and isinstance(d2, date)):
            continue
        summary = " - ".join([x for x in [r.get("appartement",""), r.get("plateforme",""), r.get("nom_client",""), r.get("telephone","")] if x])
        desc = (
            f"Appartement: {r.get('appartement','')}\\n"
            f"Plateforme: {r.get('plateforme','')}\\n"
            f"Client: {r.get('nom_client','')}\\n"
            f"Téléphone: {r.get('telephone','')}\\n"
            f"Arrivee: {format_date_str(d1)}\\n"
            f"Depart: {format_date_str(d2)}"
        )
        A("BEGIN:VEVENT")
        A(f"UID:{_ics_escape(str(r.get('ical_uid','')) or f'{r.name}@multi')}")
        A(f"DTSTAMP:{_dtstamp()}")
        A(f"DTSTART;VALUE=DATE:{_fmt_date_ics(d1)}")
        A(f"DTEND;VALUE=DATE:{_fmt_date_ics(d2)}")
        A(f"SUMMARY:{_ics_escape(summary)}")
        A(f"DESCRIPTION:{_ics_escape(desc)}")
        A("END:VEVENT")

    A("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

def vue_export_ics(df: pd.DataFrame):
    st.header("📤 Export ICS (import manuel Google Agenda)")
    if df.empty:
        st.info("Aucune donnée.")
        return

    colf = st.columns(4)
    apt_opt = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    pf_opt  = ["Toutes"] + sorted(df["plateforme"].dropna().unique().tolist())
    an_opt  = ["Toutes"] + sorted([int(x) for x in df["AAAA"].dropna().unique().tolist()])
    mois_opt= ["Tous"] + [f"{i:02d}" for i in range(1,13)]

    apt  = colf[0].selectbox("Appartement", apt_opt)
    pf   = colf[1].selectbox("Plateforme", pf_opt)
    an   = colf[2].selectbox("Année", an_opt)
    mois = colf[3].selectbox("Mois", mois_opt)

    data = df.copy()
    if apt  != "Tous":   data = data[data["appartement"] == apt]
    if pf   != "Toutes": data = data[data["plateforme"] == pf]
    if an   != "Toutes": data = data[data["AAAA"] == int(an)]
    if mois != "Tous":   data = data[data["MM"] == int(mois)]

    if data.empty:
        st.info("Aucune réservation pour ces filtres.")
        return

    ics_text = df_to_ics_multi(data)
    st.download_button(
        "⬇️ Télécharger reservations_multi.ics",
        data=ics_text.encode("utf-8"),
        file_name="reservations_multi.ics",
        mime="text/calendar"
    )
# ==============================  NAVIGATION / APP  ==============================

def render_file_section(df: pd.DataFrame):
    st.sidebar.title("📁 Fichier")
    # Boutons définis en PARTIE 1 (lecture/écriture)
    try:
        bouton_telecharger(df)
    except Exception:
        pass
    try:
        bouton_restaurer()
    except Exception:
        pass

def render_maintenance_section():
    st.sidebar.markdown("---")
    st.sidebar.markdown("## 🧰 Maintenance")
    if st.sidebar.button("♻️ Vider le cache et relancer"):
        try:
            st.cache_data.clear()
        except Exception:
            pass
        try:
            st.cache_resource.clear()
        except Exception:
            pass
        st.sidebar.success("Cache vidé. Redémarrage…")
        st.rerun()

def main():
    st.set_page_config(page_title="🏢 Réservations Multi-appartements", layout="wide")

    # Charge les données dès le démarrage
    df = charger_donnees()

    # Barre latérale : Fichier + Maintenance
    render_file_section(df)

    st.sidebar.title("🧭 Navigation")
    onglet = st.sidebar.radio(
        "Aller à",
        [
            "📋 Réservations",
            "➕ Ajouter",
            "✏️ Modifier / Supprimer",
            "📅 Calendrier",
            "📊 Rapport",
            "✉️ SMS",
            "📤 Export ICS",
        ],
        index=0,
    )

    render_maintenance_section()

    # Route vers la vue choisie
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
    elif onglet == "✉️ SMS":
        vue_sms(df)
    elif onglet == "📤 Export ICS":
        vue_export_ics(df)

if __name__ == "__main__":
    main()