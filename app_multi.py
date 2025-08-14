# app_multi.py — Villa Tobias (Multi appartements)
# Couleurs plateformes persistées dans l'Excel (onglet "Plateformes")
# Modèle financier multi :
#   brut (saisi) - commissions - frais_cb = net
#   net - menage - taxes_sejour = base
#   %commission = (commissions + frais_cb) / brut * 100

import streamlit as st
import pandas as pd
import numpy as np
import calendar
from datetime import date, timedelta, datetime, timezone
from io import BytesIO
from urllib.parse import quote
import hashlib
import os

# ========================== CONFIG =========================================
FICHIER = "reservations_multi.xlsx"  # Fichier Excel principal (créé si absent)

DEFAULT_PLATFORM_COLORS = {
    "Booking": "#1f77b4",  # bleu
    "Airbnb":  "#2ca02c",  # vert
    "Autre":   "#ff7f0e",  # orange
}

# ========================== UTILITAIRES GÉNÉRAUX ===========================

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
    """Lit le téléphone comme texte, retire .0 et espaces, conserve +."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = str(x).strip().replace(" ", "")
    if s.endswith(".0"):
        s = s[:-2]
    return s

def is_total_row(row: pd.Series) -> bool:
    # Dans le multi : on évite d'interpréter comme "Total" si un nom/client est bien rempli.
    name_is_total = str(row.get("nom_client","")).strip().lower() == "total"
    pf_is_total   = str(row.get("plateforme","")).strip().lower() == "total"
    d1 = row.get("date_arrivee"); d2 = row.get("date_depart")
    no_dates = not isinstance(d1, date) and not isinstance(d2, date)
    has_money = any(pd.notna(row.get(c)) and float(row.get(c) or 0) != 0
                    for c in ["brut","net","base","commissions","frais_cb","menage","taxes_sejour"])
    # On ne tag pas en "total" si le nom client est renseigné
    has_client = bool(str(row.get("nom_client","")).strip())
    if has_client:
        return False
    return name_is_total or pf_is_total or (no_dates and has_money)

def split_totals(df: pd.DataFrame):
    if df is None or df.empty:
        return df, df
    mask = df.apply(is_total_row, axis=1)
    return df[~mask].copy(), df[mask].copy()

def sort_core(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    by = [c for c in ["date_arrivee","nom_client"] if c in df.columns]
    return df.sort_values(by=by, na_position="last").reset_index(drop=True)

# ========================== SCHÉMA / CALCULS ===============================

def ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    base_cols = [
        "appartement",
        "nom_client","plateforme","telephone",
        "date_arrivee","date_depart","nuitees",
        # Financier multi :
        "brut","commissions","frais_cb","net","menage","taxes_sejour","base","%commission",
        # Index temporels :
        "AAAA","MM",
        # Divers :
        "ical_uid","sms_status"
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=base_cols)

    df = df.copy()

    # Dates
    for c in ["date_arrivee", "date_depart"]:
        if c in df.columns:
            df[c] = df[c].apply(to_date_only)

    # Téléphone
    if "telephone" in df.columns:
        df["telephone"] = df["telephone"].apply(normalize_tel)

    # Numériques (multi)
    for c in ["brut","commissions","frais_cb","net","menage","taxes_sejour","base","%commission"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Recalculs financiers
    # net = brut - commissions - frais_cb
    if "brut" in df.columns:
        if "commissions" not in df.columns:
            df["commissions"] = 0.0
        if "frais_cb" not in df.columns:
            df["frais_cb"] = 0.0
        df["net"] = (df["brut"] - df["commissions"] - df["frais_cb"]).round(2)

    # base = net - menage - taxes_sejour
    if "net" in df.columns:
        if "menage" not in df.columns:
            df["menage"] = 0.0
        if "taxes_sejour" not in df.columns:
            df["taxes_sejour"] = 0.0
        df["base"] = (df["net"] - df["menage"] - df["taxes_sejour"]).round(2)

    # %commission = (commissions + frais_cb) / brut * 100
    if "brut" in df.columns and "commissions" in df.columns and "frais_cb" in df.columns:
        with pd.option_context("mode.use_inf_as_na", True):
            df["%commission"] = ((df["commissions"] + df["frais_cb"]) / df["brut"] * 100).fillna(0).round(2)

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
        "appartement":"Appartement A",
        "nom_client":"",
        "plateforme":"Autre",
        "telephone":"",
        "ical_uid":"",
        "sms_status":""  # '🟢' ou '🟠'
    }
    for k, v in defaults.items():
        if k not in df.columns:
            df[k] = v

    # Arrondis
    for c in ["brut","commissions","frais_cb","net","menage","taxes_sejour","base","%commission"]:
        if c in df.columns:
            df[c] = df[c].round(2)

    # Ordonne colonnes
    cols = base_cols
    return df[[c for c in cols if c in df.columns] + [c for c in df.columns if c not in cols]]

# ========================== EXCEL I/O + Couleurs plateformes ================

@st.cache_data(show_spinner=False)
def _read_excel_cached(path: str, mtime: float):
    # Convertisseur pour téléphone
    xls = pd.ExcelFile(path)
    # Feuille réservations: on prend la première feuille non "Plateformes" si besoin
    sheet_main = "Réservations" if "Réservations" in xls.sheet_names else xls.sheet_names[0]
    df_main = pd.read_excel(xls, sheet_name=sheet_main, converters={"telephone": normalize_tel})
    df_plat = None
    if "Plateformes" in xls.sheet_names:
        df_plat = pd.read_excel(xls, sheet_name="Plateformes")
    return df_main, df_plat

def charger_donnees():
    if not os.path.exists(FICHIER):
        # Crée un fichier vide avec les colonnes de base + onglet Plateformes
        vide = ensure_schema(pd.DataFrame())
        save_platform_colors(FICHIER, DEFAULT_PLATFORM_COLORS, vide, sheet_main="Réservations")
        return vide
    try:
        mtime = os.path.getmtime(FICHIER)
        df_main, df_plat = _read_excel_cached(FICHIER, mtime)
        df = ensure_schema(df_main)
        # Charger couleurs plateformes en session
        if "platform_colors" not in st.session_state:
            st.session_state.platform_colors = load_platform_colors_from_df(df_plat)
        return df
    except Exception as e:
        st.error(f"Erreur de lecture Excel : {e}")
        return ensure_schema(pd.DataFrame())

def load_platform_colors_from_df(df_plat: pd.DataFrame | None) -> dict:
    if df_plat is None or df_plat.empty:
        return DEFAULT_PLATFORM_COLORS.copy()
    dfc = df_plat.dropna(subset=["plateforme","couleur_hex"])
    mapping = {}
    for _, r in dfc.iterrows():
        pf = str(r["plateforme"]).strip()
        col = str(r["couleur_hex"]).strip()
        if pf and col.startswith("#"):
            mapping[pf] = col
    for k, v in DEFAULT_PLATFORM_COLORS.items():
        mapping.setdefault(k, v)
    return mapping

def save_platform_colors(path: str, mapping: dict, reservations_df: pd.DataFrame, sheet_main="Réservations"):
    """Écrit:
       - la feuille des réservations (sheet_main)
       - la feuille "Plateformes" avec les couleurs
    """
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            reservations_df.to_excel(writer, index=False, sheet_name=sheet_main)
            plat_df = pd.DataFrame([{"plateforme": k, "couleur_hex": v} for k, v in mapping.items()])
            plat_df.to_excel(writer, index=False, sheet_name="Plateformes")
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Erreur sauvegarde Excel : {e}")

def sauvegarder_donnees(df: pd.DataFrame):
    df = ensure_schema(df)
    core, totals = split_totals(df)
    core = sort_core(core)
    out = pd.concat([core, totals], ignore_index=True)
    mapping = st.session_state.get("platform_colors", DEFAULT_PLATFORM_COLORS.copy())
    save_platform_colors(FICHIER, mapping, out, sheet_main="Réservations")
    st.success("💾 Sauvegarde Excel effectuée.")

def bouton_restaurer():
    up = st.sidebar.file_uploader("📤 Restauration xlsx", type=["xlsx"], help="Remplace le fichier actuel")
    if up is not None:
        try:
            xls = pd.ExcelFile(up)
            sheet_main = "Réservations" if "Réservations" in xls.sheet_names else xls.sheet_names[0]
            df_main = pd.read_excel(xls, sheet_name=sheet_main, converters={"telephone": normalize_tel})
            df_main = ensure_schema(df_main)
            # couleurs si présente
            df_plat = None
            if "Plateformes" in xls.sheet_names:
                df_plat = pd.read_excel(xls, sheet_name="Plateformes")
                st.session_state.platform_colors = load_platform_colors_from_df(df_plat)
            save_platform_colors(FICHIER, st.session_state.get("platform_colors", DEFAULT_PLATFORM_COLORS.copy()), df_main, sheet_main="Réservations")
            st.sidebar.success("✅ Fichier restauré.")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Erreur import: {e}")

def bouton_telecharger(df: pd.DataFrame):
    buf = BytesIO()
    try:
        # Exporte avec l’onglet Plateformes
        mapping = st.session_state.get("platform_colors", DEFAULT_PLATFORM_COLORS.copy())
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            ensure_schema(df).to_excel(writer, index=False, sheet_name="Réservations")
            plat_df = pd.DataFrame([{"plateforme": k, "couleur_hex": v} for k, v in mapping.items()])
            plat_df.to_excel(writer, index=False, sheet_name="Plateformes")
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

# ========================== COULEURS EN SIDEBAR ============================

def render_platform_colors_sidebar(df: pd.DataFrame):
    st.sidebar.markdown("---")
    st.sidebar.subheader("🎨 Plateformes & couleurs")

    # Plateformes trouvées (dans DF + mapping)
    plats_connues = set(st.session_state.platform_colors.keys())
    if not df.empty and "plateforme" in df.columns:
        plats_connues |= set(df["plateforme"].dropna().astype(str).tolist())

    # Edition
    new_mapping = {}
    for pf in sorted(plats_connues):
        default_col = st.session_state.platform_colors.get(pf, DEFAULT_PLATFORM_COLORS.get(pf, "#999999"))
        col = st.sidebar.color_picker(pf, default_col, key=f"pf_color_{pf}")
        new_mapping[pf] = col

    # Ajout
    with st.sidebar.expander("➕ Ajouter une plateforme"):
        new_pf = st.text_input("Nom de la plateforme", key="new_pf_name")
        new_pf_color = st.color_picker("Couleur", "#888888", key="new_pf_color")
        if st.button("Ajouter la plateforme", key="btn_add_pf"):
            npf = (new_pf or "").strip()
            if npf:
                st.session_state.platform_colors[npf] = new_pf_color
                st.rerun()

    # Enregistrer couleurs
    if st.sidebar.button("💾 Enregistrer couleurs", use_container_width=True):
        st.session_state.platform_colors = new_mapping
        # Sauvegarde (sans perdre les réservations)
        cur = charger_donnees()
        save_platform_colors(FICHIER, st.session_state.platform_colors, cur, sheet_main="Réservations")
        st.success("Couleurs sauvegardées dans l’Excel.")

def get_platform_color(pf: str) -> str:
    return st.session_state.platform_colors.get(pf, DEFAULT_PLATFORM_COLORS.get(pf, "#999999"))

# ========================== ICS EXPORT =====================================

def _ics_escape(text: str) -> str:
    if text is None:
        return ""
    s = str(text)
    s = s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    s = s.replace("\n", "\\n")
    return s

def _fmt_date_ics(d: date) -> str:
    return d.strftime("%Y%m%d")

def _dtstamp_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def _stable_uid(nom_client, plateforme, d1, d2, tel, apt, salt="v1"):
    base = f"{nom_client}|{plateforme}|{d1}|{d2}|{tel}|{apt}|{salt}"
    h = hashlib.sha1(base.encode("utf-8")).hexdigest()
    return f"vtm-{h}@villatobias"

def df_to_ics(df: pd.DataFrame, cal_name: str = "Villa Tobias – Réservations (Multi)") -> str:
    df = ensure_schema(df)
    if df.empty:
        return (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PROID:-//Villa Tobias//Reservations Multi//FR\r\n"
            f"X-WR-CALNAME:{_ics_escape(cal_name)}\r\n"
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
    A(f"X-WR-CALNAME:{_ics_escape(cal_name)}")
    A("CALSCALE:GREGORIAN")
    A("METHOD:PUBLISH")

    for _, row in core.iterrows():
        d1 = row.get("date_arrivee")
        d2 = row.get("date_depart")
        if not (isinstance(d1, date) and isinstance(d2, date)):
            continue

        pf = str(row.get("plateforme") or "").strip()
        nom = str(row.get("nom_client") or "").strip()
        tel = str(row.get("telephone") or "").strip()
        apt = str(row.get("appartement") or "").strip()

        summary = " - ".join([x for x in [apt, pf, nom, tel] if x])
        nuitees = int(row.get("nuitees") or ((d2 - d1).days))

        desc = (
            f"Appartement: {apt}\\n"
            f"Plateforme: {pf}\\n"
            f"Client: {nom}\\n"
            f"Téléphone: {tel}\\n"
            f"Arrivee: {d1.strftime('%Y/%m/%d')}\\n"
            f"Depart: {d2.strftime('%Y/%m/%d')}\\n"
            f"Nuitees: {nuitees}"
        )

        uid_existing = str(row.get("ical_uid") or "").strip()
        uid = uid_existing if uid_existing else _stable_uid(nom, pf, d1, d2, tel, apt, salt="v1")

        A("BEGIN:VEVENT")
        A(f"UID:{_ics_escape(uid)}")
        A(f"DTSTAMP:{_dtstamp_utc_now()}")
        A(f"DTSTART;VALUE=DATE:{_fmt_date_ics(d1)}")
        A(f"DTEND;VALUE=DATE:{_fmt_date_ics(d2)}")
        A(f"SUMMARY:{_ics_escape(summary)}")
        A(f"DESCRIPTION:{_ics_escape(desc)}")
        A("END:VEVENT")

    A("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

# ========================== VUES ===========================================

def totaux_chips_html(total_brut, total_net, total_base, total_nuits, pct_comm):
    return f"""
<style>
.chips-wrap {{ display:flex; flex-wrap:wrap; gap:12px; margin:8px 0 16px 0; }}
.chip {{ padding:10px 12px; border-radius:10px; background: rgba(127,127,127,0.12); border: 1px solid rgba(127,127,127,0.25); }}
.chip b {{ display:block; margin-bottom:4px; }}
</style>
<div class="chips-wrap">
  <div class="chip"><b>Total Brut (saisi)</b><div>{total_brut:,.2f} €</div></div>
  <div class="chip"><b>Total Net</b><div>{total_net:,.2f} €</div></div>
  <div class="chip"><b>Total Base</b><div>{total_base:,.2f} €</div></div>
  <div class="chip"><b>Total Nuitées</b><div>{int(total_nuits) if pd.notna(total_nuits) else 0}</div></div>
  <div class="chip"><b>% commission moy.</b><div>{pct_comm:.2f} %</div></div>
</div>
"""

def vue_reservations(df: pd.DataFrame):
    st.title("📋 Réservations (Multi)")
    core, totals = split_totals(ensure_schema(df))
    core = sort_core(core)

    if not core.empty:
        total_brut = core["brut"].sum(skipna=True)
        total_net  = core["net"].sum(skipna=True)
        total_base = core["base"].sum(skipna=True)
        total_nuit = core["nuitees"].sum(skipna=True)
        pct = ((core["commissions"].sum() + core["frais_cb"].sum()) / total_brut * 100) if total_brut else 0
        st.markdown(totaux_chips_html(total_brut, total_net, total_base, total_nuit, pct), unsafe_allow_html=True)

    show = pd.concat([core, totals], ignore_index=True).copy()
    for c in ["date_arrivee","date_depart"]:
        if c in show.columns:
            show[c] = show[c].apply(format_date_str)
    st.dataframe(show, use_container_width=True)

def vue_ajouter(df: pd.DataFrame):
    st.title("➕ Ajouter une réservation (Multi)")
    st.caption("Saisie rapide (libellés inline)")

    def inline_input(label, widget_fn, key=None, **widget_kwargs):
        col1, col2 = st.columns([1,2])
        with col1: st.markdown(f"**{label}**")
        with col2: return widget_fn(label, key=key, label_visibility="collapsed", **widget_kwargs)

    appartement = inline_input("Appartement", st.text_input, key="add_apt", value="Appartement A")
    nom = inline_input("Nom", st.text_input, key="add_nom", value="")
    tel = inline_input("Téléphone (+33...)", st.text_input, key="add_tel", value="")
    plateforme = inline_input("Plateforme", st.selectbox, key="add_pf",
                              options=sorted(list(set(["Booking","Airbnb","Autre"] + list(df["plateforme"].dropna().unique())))))

    arrivee = inline_input("Arrivée", st.date_input, key="add_arrivee", value=date.today())
    min_dep = arrivee + timedelta(days=1)
    depart  = inline_input("Départ",  st.date_input, key="add_depart",
                           value=min_dep, min_value=min_dep)

    brut = inline_input("Brut (€)", st.number_input, key="add_brut",
                        min_value=0.0, step=1.0, format="%.2f")
    commissions = inline_input("Commissions (€)", st.number_input, key="add_comm",
                               min_value=0.0, step=0.5, format="%.2f")
    frais_cb = inline_input("Frais CB (€)", st.number_input, key="add_cb",
                            min_value=0.0, step=0.5, format="%.2f")
    menage = inline_input("Ménage (€)", st.number_input, key="add_menage",
                          min_value=0.0, step=1.0, format="%.2f")
    taxes = inline_input("Taxes séjour (€)", st.number_input, key="add_taxes",
                         min_value=0.0, step=0.5, format="%.2f")

    net_calc = max(float(brut) - float(commissions) - float(frais_cb), 0.0)
    base_calc = max(net_calc - float(menage) - float(taxes), 0.0)
    pct_comm = ((float(commissions) + float(frais_cb)) / float(brut) * 100) if float(brut) > 0 else 0.0

    inline_input("Net (calculé) (€)", st.number_input, key="add_net",
                 value=round(net_calc,2), step=0.01, format="%.2f", disabled=True)
    inline_input("Base (calculée) (€)", st.number_input, key="add_base",
                 value=round(base_calc,2), step=0.01, format="%.2f", disabled=True)
    inline_input("% commission", st.number_input, key="add_pct",
                 value=round(pct_comm,2), step=0.01, format="%.2f", disabled=True)

    c1, c2 = st.columns(2)
    if c1.button("Enregistrer"):
        if depart < arrivee + timedelta(days=1):
            st.error("La date de départ doit être au moins le lendemain de l’arrivée.")
            return
        ligne = {
            "appartement": (appartement or "").strip(),
            "nom_client": (nom or "").strip(),
            "plateforme": plateforme,
            "telephone": normalize_tel(tel),
            "date_arrivee": arrivee,
            "date_depart": depart,
            "nuitees": (depart - arrivee).days,
            "brut": float(brut),
            "commissions": float(commissions),
            "frais_cb": float(frais_cb),
            "net": round(net_calc, 2),
            "menage": float(menage),
            "taxes_sejour": float(taxes),
            "base": round(base_calc, 2),
            "%commission": round(pct_comm, 2),
            "AAAA": arrivee.year,
            "MM": arrivee.month,
            "ical_uid": "",
            "sms_status": "🟠",  # par défaut: en attente
        }
        df2 = pd.concat([df, pd.DataFrame([ligne])], ignore_index=True)
        sauvegarder_donnees(df2)
        st.success("✅ Réservation enregistrée")
        st.rerun()
    with c2:
        st.info("Les champs Net/Base/% sont calculés automatiquement.")

def vue_modifier(df: pd.DataFrame):
    st.title("✏️ Modifier / Supprimer (Multi)")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune réservation.")
        return

    df["identifiant"] = (
        df["appartement"].astype(str) + " | " +
        df["nom_client"].astype(str) + " | " +
        df["date_arrivee"].apply(format_date_str)
    )
    choix = st.selectbox("Choisir une réservation", df["identifiant"])
    idx = df.index[df["identifiant"] == choix]
    if len(idx) == 0:
        st.warning("Sélection invalide.")
        return
    i = idx[0]

    ctop = st.columns(3)
    apt = ctop[0].text_input("Appartement", df.at[i,"appartement"])
    nom = ctop[1].text_input("Nom", df.at[i,"nom_client"])
    tel = ctop[2].text_input("Téléphone", normalize_tel(df.at[i,"telephone"]))

    plat_opts = sorted(list(set(["Booking","Airbnb","Autre"] + list(df["plateforme"].dropna().unique()))))
    pf = st.selectbox("Plateforme", plat_opts, index=plat_opts.index(df.at[i,"plateforme"]) if df.at[i,"plateforme"] in plat_opts else 0)

    arrivee = st.date_input("Arrivée", df.at[i,"date_arrivee"] if isinstance(df.at[i,"date_arrivee"], date) else date.today())
    depart  = st.date_input("Départ",  df.at[i,"date_depart"] if isinstance(df.at[i,"date_depart"], date) else arrivee + timedelta(days=1), min_value=arrivee+timedelta(days=1))

    c = st.columns(3)
    brut = c[0].number_input("Brut (€)", min_value=0.0, value=float(df.at[i,"brut"]) if pd.notna(df.at[i,"brut"]) else 0.0, step=1.0, format="%.2f")
    commissions = c[1].number_input("Commissions (€)", min_value=0.0, value=float(df.at[i,"commissions"]) if pd.notna(df.at[i,"commissions"]) else 0.0, step=0.5, format="%.2f")
    frais_cb = c[2].number_input("Frais CB (€)", min_value=0.0, value=float(df.at[i,"frais_cb"]) if pd.notna(df.at[i,"frais_cb"]) else 0.0, step=0.5, format="%.2f")

    c2 = st.columns(3)
    menage = c2[0].number_input("Ménage (€)", min_value=0.0, value=float(df.at[i,"menage"]) if pd.notna(df.at[i,"menage"]) else 0.0, step=1.0, format="%.2f")
    taxes  = c2[1].number_input("Taxes séjour (€)", min_value=0.0, value=float(df.at[i,"taxes_sejour"]) if pd.notna(df.at[i,"taxes_sejour"]) else 0.0, step=0.5, format="%.2f")
    sms_status = c2[2].selectbox("Statut SMS", ["", "🟠", "🟢"], index=["","🟠","🟢"].index(df.at[i,"sms_status"]) if df.at[i,"sms_status"] in ["","🟠","🟢"] else 0)

    net_calc = max(brut - commissions - frais_cb, 0.0)
    base_calc = max(net_calc - menage - taxes, 0.0)
    pct_comm = ((commissions + frais_cb) / brut * 100) if brut > 0 else 0.0
    st.markdown(f"**Net**: {net_calc:.2f} €  •  **Base**: {base_calc:.2f} €  •  **%**: {pct_comm:.2f}")

    b1, b2 = st.columns(2)
    if b1.button("💾 Enregistrer"):
        if depart < arrivee + timedelta(days=1):
            st.error("La date de départ doit être au moins le lendemain de l’arrivée.")
            return
        df.at[i,"appartement"] = apt.strip()
        df.at[i,"nom_client"] = nom.strip()
        df.at[i,"telephone"] = normalize_tel(tel)
        df.at[i,"plateforme"] = pf
        df.at[i,"date_arrivee"] = arrivee
        df.at[i,"date_depart"] = depart
        df.at[i,"nuitees"] = (depart - arrivee).days
        df.at[i,"brut"] = float(brut)
        df.at[i,"commissions"] = float(commissions)
        df.at[i,"frais_cb"] = float(frais_cb)
        df.at[i,"net"] = round(net_calc, 2)
        df.at[i,"menage"] = float(menage)
        df.at[i,"taxes_sejour"] = float(taxes)
        df.at[i,"base"] = round(base_calc, 2)
        df.at[i,"%commission"] = round(pct_comm, 2)
        df.at[i,"AAAA"] = arrivee.year
        df.at[i,"MM"] = arrivee.month
        df.at[i,"sms_status"] = sms_status
        df.drop(columns=["identifiant"], inplace=True, errors="ignore")
        sauvegarder_donnees(df)
        st.success("✅ Modifié")
        st.rerun()

    if b2.button("🗑 Supprimer"):
        df2 = df.drop(index=i)
        df2.drop(columns=["identifiant"], inplace=True, errors="ignore")
        sauvegarder_donnees(df2)
        st.warning("Supprimé.")
        st.rerun()

def vue_calendrier(df: pd.DataFrame):
    st.title("📅 Calendrier mensuel (Multi)")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    c1, c2 = st.columns(2)
    mois_nom = c1.selectbox("Mois", list(calendar.month_name)[1:], index=max(0, date.today().month-1))
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    if not annees:
        st.warning("Aucune année disponible.")
        return
    annee = c2.selectbox("Année", annees, index=len(annees)-1)

    mois_index = list(calendar.month_name).index(mois_nom)
    nb_jours = calendar.monthrange(annee, mois_index)[1]
    jours = [date(annee, mois_index, j+1) for j in range(nb_jours)]
    planning = {j: [] for j in jours}

    # Utilisons des pastilles colorées HTML en fonction des couleurs
    def badge(pf, nom):
        col = get_platform_color(pf)
        dot = f"<span style='display:inline-block;width:0.8em;height:0.8em;border-radius:50%;background:{col};margin-right:6px;'></span>"
        return f"{dot}{nom}"

    core, _ = split_totals(df)
    for _, row in core.iterrows():
        d1 = row["date_arrivee"]; d2 = row["date_depart"]
        if not (isinstance(d1, date) and isinstance(d2, date)): 
            continue
        for j in jours:
            if d1 <= j < d2:
                planning[j].append(badge(row["plateforme"], str(row["nom_client"])))

    table = []
    for semaine in calendar.monthcalendar(annee, mois_index):
        ligne = []
        for jour in semaine:
            if jour == 0:
                ligne.append("")
            else:
                d = date(annee, mois_index, jour)
                contenu = f"<div style='font-weight:600'>{jour}</div>" + "<br/>".join(planning.get(d, []))
                ligne.append(contenu)
        table.append(ligne)

    df_show = pd.DataFrame(table, columns=["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"])
    st.write(df_show.to_html(escape=False), unsafe_allow_html=True)

def vue_rapport(df: pd.DataFrame):
    st.title("📊 Rapport (Multi)")
    import matplotlib.pyplot as plt

    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    # Filtres
    c1, c2, c3, c4 = st.columns(4)
    apt_opts = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    apt = c1.selectbox("Appartement", apt_opts)
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    annee = c2.selectbox("Année", annees, index=len(annees)-1) if annees else None
    pf_opt = ["Toutes"] + sorted(df["plateforme"].dropna().unique().tolist())
    pf = c3.selectbox("Plateforme", pf_opt)
    mois_opt = ["Tous"] + [f"{i:02d}" for i in range(1,13)]
    mois_label = c4.selectbox("Mois", mois_opt)

    data = df.copy()
    if apt != "Tous":
        data = data[data["appartement"] == apt]
    if annee is not None:
        data = data[data["AAAA"] == int(annee)]
    if pf != "Toutes":
        data = data[data["plateforme"] == pf]
    if mois_label != "Tous":
        data = data[data["MM"] == int(mois_label)]
    if data.empty:
        st.info("Aucune donnée pour ces filtres.")
        return

    # Détail
    detail = data.copy()
    for c in ["date_arrivee","date_depart"]:
        detail[c] = detail[c].apply(format_date_str)
    by = [c for c in ["date_arrivee","nom_client"] if c in detail.columns]
    if by:
        detail = detail.sort_values(by=by, na_position="last").reset_index(drop=True)

    cols_detail = [
        "appartement","nom_client","plateforme","telephone",
        "date_arrivee","date_depart","nuitees",
        "brut","commissions","frais_cb","net","menage","taxes_sejour","base","%commission"
    ]
    cols_detail = [c for c in cols_detail if c in detail.columns]
    st.dataframe(detail[cols_detail], use_container_width=True)

    # Totaux
    total_brut = data["brut"].sum(skipna=True)
    total_net  = data["net"].sum(skipna=True)
    total_base = data["base"].sum(skipna=True)
    total_nuit = data["nuitees"].sum(skipna=True)
    pct = ((data["commissions"].sum() + data["frais_cb"].sum()) / total_brut * 100) if total_brut else 0
    st.markdown(totaux_chips_html(total_brut, total_net, total_base, total_nuit, pct), unsafe_allow_html=True)

    # Agrégation mensuelle / plateforme
    stats = (
        data.groupby(["MM","plateforme"], dropna=True)
            .agg(brut=("brut","sum"),
                 net=("net","sum"),
                 base=("base","sum"),
                 nuitees=("nuitees","sum"))
            .reset_index()
    )
    if stats.empty:
        st.info("Aucune donnée après agrégation.")
        return

    # Graphes (matplotlib) avec couleurs par plateforme
    months = list(range(1,13))
    plats = sorted(stats["plateforme"].unique().tolist())
    base_x = np.arange(len(months), dtype=float)

    def plot_grouped(metric: str, title: str, ylabel: str):
        width = 0.8 / max(1, len(plats))
        fig, ax = plt.subplots(figsize=(10, 4))
        for i, p in enumerate(plats):
            sub = stats[stats["plateforme"] == p]
            vals = {int(mm): float(v) for mm, v in zip(sub["MM"], sub[metric])}
            y = np.array([vals.get(m, 0.0) for m in months], dtype=float)
            x = base_x + (i - (len(plats)-1)/2) * width
            ax.bar(x, y, width=width, label=p, color=get_platform_color(p))
        ax.set_xlim(-0.5, 11.5)
        ax.set_xticks(base_x)
        ax.set_xticklabels([f"{m:02d}" for m in months])
        ax.set_xlabel(f"Mois{'' if annee is None else ' ('+str(annee)+')'}")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.legend(loc="upper left", frameon=False)
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        st.pyplot(fig)
        plt.close(fig)

    plot_grouped("brut", "💰 Brut (saisi)", "€")
    plot_grouped("net",  "💸 Net (après commissions+CB)", "€")
    plot_grouped("base", "🏁 Base (après ménage+taxes)", "€")
    plot_grouped("nuitees", "🛌 Nuitées", "Nuitées")

    # Export du détail filtré en XLSX
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        detail[cols_detail].to_excel(writer, index=False)
    st.download_button(
        "⬇️ Télécharger le détail (XLSX)",
        data=buf.getvalue(),
        file_name=f"rapport_multi_{('tous' if annee is None else annee)}_{pf}_{mois_label}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

def vue_sms(df: pd.DataFrame):
    st.title("✉️ SMS (envoi manuel) — Multi")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    today = date.today()
    demain = today + timedelta(days=1)
    hier = today - timedelta(days=1)

    def sms_message_arrivee(row: pd.Series) -> str:
        d1 = row.get("date_arrivee"); d2 = row.get("date_depart")
        d1s = d1.strftime("%Y/%m/%d") if isinstance(d1, date) else ""
        d2s = d2.strftime("%Y/%m/%d") if isinstance(d2, date) else ""
        nuitees = int(row.get("nuitees") or ((d2 - d1).days if isinstance(d1, date) and isinstance(d2, date) else 0))
        pf = str(row.get("plateforme") or "")
        nom = str(row.get("nom_client") or "")
        tel_aff = str(row.get("telephone") or "").strip()
        apt = str(row.get("appartement") or "")
        return (
            "VILLA TOBIAS\n"
            f"Appartement : {apt}\n"
            f"Plateforme : {pf}\n"
            f"Date d'arrivee : {d1s}  Date depart : {d2s}  Nombre de nuitees : {nuitees}\n\n"
            f"Bonjour {nom}\n"
            f"Telephone : {tel_aff}\n\n"
            "Bienvenue chez nous ! Nous sommes ravis de vous accueillir bientot.\n"
            "Pour organiser au mieux votre reception, indiquez-nous votre heure d'arrivee.\n"
            "Une place de parking est disponible si besoin.\n\n"
            "Annick & Charley"
        )

    def sms_message_depart(row: pd.Series) -> str:
        nom = str(row.get("nom_client") or "")
        return (
            f"Bonjour {nom},\n\n"
            "Merci d’avoir choisi notre appartement ! Nous espérons que votre séjour s’est bien passé.\n"
            "Si vous souhaitez revenir, notre porte est toujours ouverte.\n\n"
            "Au plaisir de vous accueillir à nouveau,\n"
            "Annick & Charley"
        )

    colA, colB = st.columns(2)
    with colA:
        st.subheader("📆 Arrivées demain")
        arrives = df[df["date_arrivee"] == demain].copy()
        if arrives.empty:
            st.info("Aucune arrivée demain.")
        else:
            for idx, r in arrives.reset_index(drop=True).iterrows():
                body = sms_message_arrivee(r)
                tel = normalize_tel(r.get("telephone"))
                tel_link = f"tel:{tel}" if tel else ""
                sms_link = f"sms:{tel}?&body={quote(body)}" if tel and body else ""
                st.markdown(f"**{r.get('nom_client','')}** — {r.get('appartement','')} — {r.get('plateforme','')}")
                st.markdown(f"Arrivée: {format_date_str(r.get('date_arrivee'))} • Départ: {format_date_str(r.get('date_depart'))} • Nuits: {r.get('nuitees','')}")
                st.code(body)
                c1, c2, c3 = st.columns([1,1,2])
                ck_call = c1.checkbox("📞 Appeler", key=f"m_sms_arr_call_{idx}", value=False)
                ck_sms  = c2.checkbox("📩 SMS", key=f"m_sms_arr_sms_{idx}", value=True)
                with c3:
                    if ck_call and tel_link:
                        st.link_button(f"Appeler {tel}", tel_link)
                    if ck_sms and sms_link:
                        st.link_button("Envoyer SMS", sms_link)
                st.divider()

    with colB:
        st.subheader("🕒 Relance +24h après départ")
        dep_24h = df[df["date_depart"] == hier].copy()
        if dep_24h.empty:
            st.info("Aucun départ hier.")
        else:
            for idx, r in dep_24h.reset_index(drop=True).iterrows():
                body = sms_message_depart(r)
                tel = normalize_tel(r.get("telephone"))
                tel_link = f"tel:{tel}" if tel else ""
                sms_link = f"sms:{tel}?&body={quote(body)}" if tel and body else ""
                st.markdown(f"**{r.get('nom_client','')}** — {r.get('appartement','')} — {r.get('plateforme','')}")
                st.code(body)
                c1, c2, c3 = st.columns([1,1,2])
                ck_call = c1.checkbox("📞 Appeler", key=f"m_sms_dep_call_{idx}", value=False)
                ck_sms  = c2.checkbox("📩 SMS", key=f"m_sms_dep_sms_{idx}", value=True)
                with c3:
                    if ck_call and tel_link:
                        st.link_button(f"Appeler {tel}", tel_link)
                    if ck_sms and sms_link:
                        st.link_button("Envoyer SMS", sms_link)
                st.divider()

    # Composeur manuel
    st.subheader("✍️ Composer un SMS manuel")
    df_pick = df.copy()
    df_pick["id_aff"] = (
        df_pick["appartement"].astype(str) + " | " +
        df_pick["nom_client"].astype(str) + " | " +
        df_pick["plateforme"].astype(str) + " | " +
        df_pick["date_arrivee"].apply(format_date_str)
    )
    choix = st.selectbox("Choisir une réservation", df_pick["id_aff"])
    r = df_pick.loc[df_pick["id_aff"] == choix].iloc[0]
    tel = normalize_tel(r.get("telephone"))

    choix_type = st.radio("Modèle de message",
                          ["Arrivée (demande d’heure)","Relance après départ","Message libre"],
                          horizontal=True)
    if choix_type == "Arrivée (demande d’heure)":
        body = sms_message_arrivee(r)
    elif choix_type == "Relance après départ":
        body = sms_message_depart(r)
    else:
        body = st.text_area("Votre message", value="", height=160, placeholder="Tapez votre SMS ici…")

    c1, c2, c3 = st.columns([2,1,1])
    with c1:
        st.code(body or "—")
    ck_call = c2.checkbox("📞 Appeler", key="m_sms_manual_call", value=False)
    ck_sms  = c3.checkbox("📩 SMS", key="m_sms_manual_sms", value=True)

    if tel and body:
        if ck_call:
            st.link_button(f"Appeler {tel}", f"tel:{tel}")
        if ck_sms:
            st.link_button("Envoyer SMS", f"sms:{tel}?&body={quote(body)}")
    else:
        st.info("Renseignez un téléphone et un message.")

def vue_export_ics(df: pd.DataFrame):
    st.title("📤 Export ICS (Google Agenda – Import manuel)")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée à exporter.")
        return

    c1, c2, c3, c4 = st.columns(4)
    apt_opts = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    apt = c1.selectbox("Appartement", apt_opts)
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    annee = c2.selectbox("Année", ["Toutes"] + annees, index=(len(annees) if annees else 0))
    mois  = c3.selectbox("Mois", ["Tous"] + list(range(1,13)))
    pfopt = ["Toutes"] + sorted(df["plateforme"].dropna().unique().tolist())
    pf    = c4.selectbox("Plateforme", pfopt)

    data = df.copy()
    if apt != "Tous":
        data = data[data["appartement"] == apt]
    if annee != "Toutes":
        data = data[data["AAAA"] == int(annee)]
    if mois != "Tous":
        data = data[data["MM"] == int(mois)]
    if pf != "Toutes":
        data = data[data["plateforme"] == pf]

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
    st.caption("Dans Google Agenda : Paramètres → Importer & exporter → Importer → sélectionnez ce fichier .ics.")

# ========================== MAINTENANCE / CACHE ============================

def render_cache_section_sidebar():
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

# ========================== APP ===========================================

def main():
    st.set_page_config(page_title="📖 Réservations (Multi)", layout="wide")

    # Fichier / Restauration / Sauvegarde
    st.sidebar.title("📁 Fichier")
    df_tmp = charger_donnees()
    bouton_telecharger(df_tmp)
    bouton_restaurer()

    # Navigation
    st.sidebar.title("🧭 Navigation")
    onglet = st.sidebar.radio(
        "Aller à",
        ["📋 Réservations","➕ Ajouter","✏️ Modifier / Supprimer",
         "📅 Calendrier","📊 Rapport","✉️ SMS","📤 Export ICS"]
    )

    # Couleurs plateformes
    if "platform_colors" not in st.session_state:
        st.session_state.platform_colors = DEFAULT_PLATFORM_COLORS.copy()
    render_platform_colors_sidebar(df_tmp)

    # Maintenance (vider cache)
    render_cache_section_sidebar()

    # Recharge données après éventuelle restauration
    df = charger_donnees()

    # Route
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