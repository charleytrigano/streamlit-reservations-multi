# app_multi.py — Réservations Multi (COMPLET)
# Modèle financier :
#   Net  = Brut - commissions - frais_cb
#   Base = Net - menage - taxes_sejour
#   %    = (commissions + frais_cb) / Brut * 100

import streamlit as st
import pandas as pd
import numpy as np
import calendar
from datetime import date, timedelta, datetime, timezone
from io import BytesIO
import hashlib
import os
from urllib.parse import quote

FICHIER = "reservations_multi.xlsx"

# ==============================  MAINTENANCE / CACHE  ==============================

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

# ==============================  OUTILS  ==============================

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
    """Forcer téléphone en TEXTE, retirer .0 éventuel, espaces, conserver le +."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = str(x).strip()
    s = s.replace(" ", "")
    if s.endswith(".0"):
        s = s[:-2]
    return s

def platform_color(pf: str) -> str:
    """Retourne un emoji carré couleur pour la plateforme (configurable en session)."""
    if "pf_colors" not in st.session_state:
        st.session_state.pf_colors = {
            "Booking": "🟦",
            "Airbnb": "🟩",
            "Autre": "🟧",
        }
    return st.session_state.pf_colors.get(pf or "Autre", "⬜")

def ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    base_cols = [
        "appartement","nom_client","plateforme","telephone",
        "date_arrivee","date_depart","nuitees",
        "brut","commissions","frais_cb","net","menage","taxes_sejour","base","%",
        "AAAA","MM","sms_status","ical_uid"
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=base_cols)

    df = df.copy()

    # Colonnes minimales
    defaults = {
        "appartement": "",
        "nom_client": "",
        "plateforme": "Autre",
        "telephone": "",
        "brut": np.nan,
        "commissions": np.nan,
        "frais_cb": np.nan,
        "net": np.nan,
        "menage": np.nan,
        "taxes_sejour": np.nan,
        "base": np.nan,
        "%": np.nan,
        "sms_status": "🟠 en attente",
        "ical_uid": ""
    }
    for k, v in defaults.items():
        if k not in df.columns:
            df[k] = v

    # Dates -> date pure
    for c in ["date_arrivee", "date_depart"]:
        if c in df.columns:
            df[c] = df[c].apply(to_date_only)

    # Numériques (saisie)
    for c in ["brut","commissions","frais_cb","menage","taxes_sejour"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Calculs financiers
    df["net"]  = (df["brut"] - df["commissions"] - df["frais_cb"]).round(2)
    df["base"] = (df["net"] - df["menage"] - df["taxes_sejour"]).round(2)
    with pd.option_context("mode.use_inf_as_na", True):
        df["%"] = ((df["commissions"] + df["frais_cb"]) / df["brut"] * 100).replace([np.inf, -np.inf], np.nan).fillna(0).round(2)

    # Nuitées
    if "date_arrivee" in df.columns and "date_depart" in df.columns:
        df["nuitees"] = [
            (d2 - d1).days if (isinstance(d1, date) and isinstance(d2, date)) else np.nan
            for d1, d2 in zip(df["date_arrivee"], df["date_depart"])
        ]

    # AAAA / MM
    df["AAAA"] = df["date_arrivee"].apply(lambda d: d.year if isinstance(d, date) else np.nan).astype("Int64")
    df["MM"]   = df["date_arrivee"].apply(lambda d: d.month if isinstance(d, date) else np.nan).astype("Int64")

    # Téléphone: chaîne nettoyée
    if "telephone" in df.columns:
        df["telephone"] = df["telephone"].apply(normalize_tel)

    cols = base_cols
    return df[[c for c in cols if c in df.columns] + [c for c in df.columns if c not in cols]]

def is_total_row(row: pd.Series) -> bool:
    name_is_total = str(row.get("nom_client","")).strip().lower() == "total"
    pf_is_total   = str(row.get("plateforme","")).strip().lower() == "total"
    d1 = row.get("date_arrivee"); d2 = row.get("date_depart")
    no_dates = not isinstance(d1, date) and not isinstance(d2, date)
    has_money = any(pd.notna(row.get(c)) and float(row.get(c) or 0) != 0 for c in ["brut","net","base"])
    return name_is_total or pf_is_total or (no_dates and has_money)

def split_totals(df: pd.DataFrame):
    if df is None or df.empty:
        return df, df
    mask = df.apply(is_total_row, axis=1)
    return df[~mask].copy(), df[mask].copy()

def sort_core(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    by = [c for c in ["date_arrivee","appartement","nom_client"] if c in df.columns]
    return df.sort_values(by=by, na_position="last").reset_index(drop=True)

# ==============================  EXCEL I/O  ==============================

@st.cache_data(show_spinner=False)
def _read_excel_cached(path: str, mtime: float):
    # Important: converter pour 'telephone'
    return pd.read_excel(path, converters={"telephone": normalize_tel})

def charger_donnees() -> pd.DataFrame:
    if not os.path.exists(FICHIER):
        # créer un modèle vide
        df0 = pd.DataFrame(columns=[
            "appartement","nom_client","plateforme","telephone",
            "date_arrivee","date_depart",
            "brut","commissions","frais_cb","menage","taxes_sejour",
            "sms_status"
        ])
        try:
            with pd.ExcelWriter(FICHIER, engine="openpyxl") as w:
                df0.to_excel(w, index=False)
        except Exception:
            pass

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
        ws = writer.sheets.get(sheet_name) or writer.sheets.get('Sheet1')
        if ws is None or "telephone" not in df_to_save.columns:
            return
        col_idx = df_to_save.columns.get_loc("telephone") + 1  # 1-based
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=col_idx, max_col=col_idx):
            cell = row[0]
            cell.number_format = '@'
    except Exception:
        pass

def sauvegarder_donnees(df: pd.DataFrame):
    df = ensure_schema(df)
    core, totals = split_totals(df)
    core = sort_core(core)
    out = pd.concat([core, totals], ignore_index=True)
    try:
        with pd.ExcelWriter(FICHIER, engine="openpyxl") as w:
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
        file_name=FICHIER,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=(data_xlsx is None),
    )

# ==============================  ICS EXPORT  ==============================

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

def _stable_uid(row, salt="v1"):
    d1, d2 = row.get("date_arrivee"), row.get("date_depart")
    base = f"{row.get('appartement')}|{row.get('nom_client')}|{row.get('plateforme')}|{d1}|{d2}|{row.get('telephone')}|{salt}"
    h = hashlib.sha1(base.encode("utf-8")).hexdigest()
    return f"vtm-{h}@villatobias"

def df_to_ics(df: pd.DataFrame, cal_name: str = "Multi – Réservations") -> str:
    df = ensure_schema(df)
    if df.empty:
        return (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PROID:-//Multi//Reservations//FR\r\n"
            f"X-WR-CALNAME:{_ics_escape(cal_name)}\r\n"
            "CALSCALE:GREGORIAN\r\n"
            "METHOD:PUBLISH\r\n"
            "END:VCALENDAR\r\n"
        )
    lines = []
    A = lines.append
    A("BEGIN:VCALENDAR")
    A("VERSION:2.0")
    A("PRODID:-//Multi//Reservations//FR")
    A(f"X-WR-CALNAME:{_ics_escape(cal_name)}")
    A("CALSCALE:GREGORIAN")
    A("METHOD:PUBLISH")

    core, _ = split_totals(df)
    core = sort_core(core)
    for _, r in core.iterrows():
        d1 = r.get("date_arrivee"); d2 = r.get("date_depart")
        if not (isinstance(d1, date) and isinstance(d2, date)):
            continue
        app = str(r.get("appartement") or "").strip()
        pf  = str(r.get("plateforme") or "").strip()
        nom = str(r.get("nom_client") or "").strip()
        tel = str(r.get("telephone") or "").strip()

        summary = " - ".join([x for x in [app, pf, nom, tel] if x])
        desc = (
            f"Appartement: {app}\\n"
            f"Plateforme: {pf}\\n"
            f"Client: {nom}\\n"
            f"Téléphone: {tel}\\n"
            f"Arrivee: {d1.strftime('%Y/%m/%d')}\\n"
            f"Depart: {d2.strftime('%Y/%m/%d')}\\n"
            f"Nuitees: {int(r.get('nuitees') or (d2-d1).days)}\\n"
            f"Brut: {float(r.get('brut') or 0):.2f} €\\n"
            f"Net: {float(r.get('net') or 0):.2f} €\\n"
            f"Base: {float(r.get('base') or 0):.2f} €"
        )

        uid_existing = str(r.get("ical_uid") or "").strip()
        uid = uid_existing if uid_existing else _stable_uid(r)

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

# ==============================  TEMPLATES SMS (MANUEL) ====================

def sms_message_arrivee(row: pd.Series) -> str:
    d1 = row.get("date_arrivee"); d2 = row.get("date_depart")
    d1s = d1.strftime("%Y/%m/%d") if isinstance(d1, date) else ""
    d2s = d2.strftime("%Y/%m/%d") if isinstance(d2, date) else ""
    nuitees = int(row.get("nuitees") or ((d2 - d1).days if isinstance(d1, date) and isinstance(d2, date) else 0))
    pf = str(row.get("plateforme") or "")
    nom = str(row.get("nom_client") or "")
    tel_aff = str(row.get("telephone") or "").strip()

    return (
        "VILLA TOBIAS\n"
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

# ==============================  UI SECTIONS  ==============================

def sidebar_platform_manager(df: pd.DataFrame):
    st.sidebar.markdown("---")
    st.sidebar.markdown("## 🎨 Plateformes")
    if "pf_colors" not in st.session_state:
        st.session_state.pf_colors = {"Booking":"🟦","Airbnb":"🟩","Autre":"🟧"}

    # Liste des plateformes existantes
    pfs = sorted([p for p in df["plateforme"].dropna().unique().tolist() if p])
    if pfs:
        st.sidebar.write("Connues :", ", ".join(pfs))

    with st.sidebar.expander("➕ Ajouter/éditer une plateforme"):
        new_pf = st.text_input("Nom de la plateforme")
        color_emoji = st.selectbox(
            "Couleur (emoji)",
            ["🟦","🟩","🟧","🟥","🟪","🟨","⬛","⬜","🟫","🟦‍⬛"],
            index=0
        )
        if st.button("Enregistrer plateforme"):
            if new_pf.strip():
                st.session_state.pf_colors[new_pf.strip()] = color_emoji
                st.success(f"Plateforme '{new_pf.strip()}' ajoutée avec {color_emoji}")

def totaux_html(brut, net, base, nuits, pct):
    return f"""
<style>
.chips-wrap {{ display:flex; flex-wrap:wrap; gap:12px; margin:8px 0 16px 0; }}
.chip {{ padding:10px 12px; border-radius:10px; background: rgba(127,127,127,0.12); border: 1px solid rgba(127,127,127,0.25); }}
.chip b {{ display:block; margin-bottom:4px; }}
</style>
<div class="chips-wrap">
  <div class="chip"><b>Total Brut</b><div>{brut:,.2f} €</div></div>
  <div class="chip"><b>Total Net</b><div>{net:,.2f} €</div></div>
  <div class="chip"><b>Total Base</b><div>{base:,.2f} €</div></div>
  <div class="chip"><b>Total Nuitées</b><div>{int(nuits) if pd.notna(nuits) else 0}</div></div>
  <div class="chip"><b>% moyen (comm+CB / Brut)</b><div>{pct:.2f} %</div></div>
</div>
"""

def vue_reservations(df: pd.DataFrame):
    st.title("📋 Réservations (multi-appartements)")
    core, totals = split_totals(ensure_schema(df))
    core = sort_core(core)

    # Filtres
    c1, c2, c3 = st.columns(3)
    apps = ["Tous"] + sorted([a for a in core["appartement"].dropna().unique().tolist() if a])
    pfopt = ["Toutes"] + sorted([p for p in core["plateforme"].dropna().unique().tolist() if p])
    years = ["Toutes"] + sorted([int(x) for x in core["AAAA"].dropna().unique()])
    app = c1.selectbox("Appartement", apps)
    pf  = c2.selectbox("Plateforme", pfopt)
    an  = c3.selectbox("Année", years)

    dat = core.copy()
    if app != "Tous":
        dat = dat[dat["appartement"] == app]
    if pf != "Toutes":
        dat = dat[dat["plateforme"] == pf]
    if an != "Toutes":
        dat = dat[dat["AAAA"] == int(an)]

    # Totaux
    if not dat.empty:
        t_brut = dat["brut"].sum(skipna=True)
        t_net  = dat["net"].sum(skipna=True)
        t_base = dat["base"].sum(skipna=True)
        t_nuit = dat["nuitees"].sum(skipna=True)
        t_pct  = ((dat["commissions"].sum()+dat["frais_cb"].sum())/dat["brut"].sum()*100) if dat["brut"].sum() else 0
        st.markdown(totaux_html(t_brut, t_net, t_base, t_nuit, t_pct), unsafe_allow_html=True)

    # Affichage
    show = pd.concat([dat, totals], ignore_index=True)
    for c in ["date_arrivee","date_depart"]:
        show[c] = show[c].apply(format_date_str)
    cols = ["appartement","nom_client","plateforme","telephone","date_arrivee","date_depart","nuitees",
            "brut","commissions","frais_cb","net","menage","taxes_sejour","base","%","sms_status"]
    cols = [c for c in cols if c in show.columns]
    st.dataframe(show[cols], use_container_width=True)

def vue_ajouter(df: pd.DataFrame):
    st.title("➕ Ajouter une réservation")

    # plateformes existantes + ajoutées
    pf_known = sorted(list(set((ensure_schema(df)["plateforme"].dropna().unique().tolist() or []) + list(st.session_state.get("pf_colors", {}).keys()))))
    if not pf_known:
        pf_known = ["Booking","Airbnb","Autre"]

    colA, colB = st.columns(2)
    appartement = colA.text_input("Appartement")
    plateforme  = colB.selectbox("Plateforme", pf_known, index=0)

    col1, col2 = st.columns(2)
    nom = col1.text_input("Nom client")
    tel = col2.text_input("Téléphone (+33...)")

    col3, col4 = st.columns(2)
    arrivee = col3.date_input("Arrivée", value=date.today())
    depart  = col4.date_input("Départ", value=arrivee+timedelta(days=1), min_value=arrivee+timedelta(days=1))

    col5, col6 = st.columns(2)
    brut = col5.number_input("Brut (€)", min_value=0.0, step=1.0, format="%.2f")
    commissions = col6.number_input("Commissions (€)", min_value=0.0, step=0.5, format="%.2f")

    col7, col8 = st.columns(2)
    frais_cb = col7.number_input("Frais CB (€)", min_value=0.0, step=0.5, format="%.2f")
    menage   = col8.number_input("Ménage (€)", min_value=0.0, step=0.5, format="%.2f")

    taxes_sejour = st.number_input("Taxes de séjour (€)", min_value=0.0, step=0.5, format="%.2f")

    # Calculs dynamiques
    net  = max(brut - commissions - frais_cb, 0.0)
    base = max(net - menage - taxes_sejour, 0.0)
    pct  = ((commissions + frais_cb) / brut * 100) if brut > 0 else 0.0

    st.caption("Aperçu (auto)")
    cprev1, cprev2, cprev3 = st.columns(3)
    cprev1.metric("Net", f"{net:.2f} €")
    cprev2.metric("Base", f"{base:.2f} €")
    cprev3.metric("% (comm+CB / Brut)", f"{pct:.2f} %")

    if st.button("Enregistrer"):
        if depart < arrivee + timedelta(days=1):
            st.error("La date de départ doit être au moins le lendemain de l’arrivée.")
            return
        ligne = {
            "appartement": (appartement or "").strip(),
            "nom_client": (nom or "").strip(),
            "plateforme": (plateforme or "Autre").strip(),
            "telephone": normalize_tel(tel),
            "date_arrivee": arrivee,
            "date_depart": depart,
            "brut": float(brut),
            "commissions": float(commissions),
            "frais_cb": float(frais_cb),
            "menage": float(menage),
            "taxes_sejour": float(taxes_sejour),
            # calculés
            "net": round(net, 2),
            "base": round(base, 2),
            "%": round(pct, 2),
            "nuitees": (depart - arrivee).days,
            "AAAA": arrivee.year,
            "MM": arrivee.month,
            "sms_status": "🟠 en attente",
            "ical_uid": ""
        }
        df2 = pd.concat([df, pd.DataFrame([ligne])], ignore_index=True)
        sauvegarder_donnees(df2)
        st.success("✅ Réservation enregistrée")
        st.rerun()

def vue_modifier(df: pd.DataFrame):
    st.title("✏️ Modifier / Supprimer")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune réservation.")
        return

    df["identifiant"] = df["appartement"].astype(str) + " | " + df["nom_client"].astype(str) + " | " + df["date_arrivee"].apply(format_date_str)
    choix = st.selectbox("Choisir une réservation", df["identifiant"])
    idx = df.index[df["identifiant"] == choix]
    if len(idx) == 0:
        st.warning("Sélection invalide.")
        return
    i = idx[0]

    col0, col00 = st.columns(2)
    appartement = col0.text_input("Appartement", df.at[i,"appartement"])
    plateformes = sorted(list(set((ensure_schema(df)["plateforme"].dropna().unique().tolist() or []) + list(st.session_state.get("pf_colors", {}).keys()))))
    if not plateformes:
        plateformes = ["Booking","Airbnb","Autre"]
    plateforme  = col00.selectbox("Plateforme", plateformes, index = (plateformes.index(df.at[i,"plateforme"]) if df.at[i,"plateforme"] in plateformes else 0))

    col1, col2 = st.columns(2)
    nom = col1.text_input("Nom client", df.at[i,"nom_client"])
    tel = col2.text_input("Téléphone", normalize_tel(df.at[i,"telephone"]))

    col3, col4 = st.columns(2)
    arrivee = col3.date_input("Arrivée", df.at[i,"date_arrivee"] if isinstance(df.at[i,"date_arrivee"], date) else date.today())
    depart  = col4.date_input("Départ",  df.at[i,"date_depart"] if isinstance(df.at[i,"date_depart"], date) else arrivee+timedelta(days=1), min_value=arrivee+timedelta(days=1))

    col5, col6 = st.columns(2)
    brut = col5.number_input("Brut (€)", min_value=0.0, value=float(df.at[i,"brut"]) if pd.notna(df.at[i,"brut"]) else 0.0, step=1.0, format="%.2f")
    commissions = col6.number_input("Commissions (€)", min_value=0.0, value=float(df.at[i,"commissions"]) if pd.notna(df.at[i,"commissions"]) else 0.0, step=0.5, format="%.2f")

    col7, col8 = st.columns(2)
    frais_cb = col7.number_input("Frais CB (€)", min_value=0.0, value=float(df.at[i,"frais_cb"]) if pd.notna(df.at[i,"frais_cb"]) else 0.0, step=0.5, format="%.2f")
    menage   = col8.number_input("Ménage (€)",   min_value=0.0, value=float(df.at[i,"menage"]) if pd.notna(df.at[i,"menage"]) else 0.0, step=0.5, format="%.2f")

    taxes_sejour = st.number_input("Taxes de séjour (€)", min_value=0.0, value=float(df.at[i,"taxes_sejour"]) if pd.notna(df.at[i,"taxes_sejour"]) else 0.0, step=0.5, format="%.2f")

    # Aperçu
    net  = max(brut - commissions - frais_cb, 0.0)
    base = max(net - menage - taxes_sejour, 0.0)
    pct  = ((commissions + frais_cb) / brut * 100) if brut > 0 else 0.0
    cprev1, cprev2, cprev3 = st.columns(3)
    cprev1.metric("Net", f"{net:.2f} €")
    cprev2.metric("Base", f"{base:.2f} €")
    cprev3.metric("% (comm+CB / Brut)", f"{pct:.2f} %")

    cA, cB = st.columns(2)
    if cA.button("💾 Enregistrer"):
        if depart < arrivee + timedelta(days=1):
            st.error("La date de départ doit être au moins le lendemain de l’arrivée.")
            return
        df.at[i,"appartement"] = appartement.strip()
        df.at[i,"plateforme"]  = plateforme.strip()
        df.at[i,"nom_client"]  = nom.strip()
        df.at[i,"telephone"]   = normalize_tel(tel)
        df.at[i,"date_arrivee"]= arrivee
        df.at[i,"date_depart"] = depart
        df.at[i,"brut"]        = float(brut)
        df.at[i,"commissions"] = float(commissions)
        df.at[i,"frais_cb"]    = float(frais_cb)
        df.at[i,"menage"]      = float(menage)
        df.at[i,"taxes_sejour"]= float(taxes_sejour)
        # calculés
        df.at[i,"net"]  = round(net, 2)
        df.at[i,"base"] = round(base, 2)
        df.at[i,"%"]    = round(pct, 2)
        df.at[i,"nuitees"] = (depart - arrivee).days
        df.at[i,"AAAA"]    = arrivee.year
        df.at[i,"MM"]      = arrivee.month
        df.drop(columns=["identifiant"], inplace=True, errors="ignore")
        sauvegarder_donnees(df)
        st.success("✅ Modifié")
        st.rerun()

    if cB.button("🗑 Supprimer"):
        df2 = df.drop(index=i)
        df2.drop(columns=["identifiant"], inplace=True, errors="ignore")
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
    apps = ["Tous"] + sorted([a for a in df["appartement"].dropna().unique().tolist() if a])
    app = c0.selectbox("Appartement", apps)
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

    core, _ = split_totals(df)
    if app != "Tous":
        core = core[core["appartement"] == app]

    for _, row in core.iterrows():
        d1 = row["date_arrivee"]; d2 = row["date_depart"]
        if not (isinstance(d1, date) and isinstance(d2, date)):
            continue
        if not (d1.year == annee or d2.year == annee or (d1 < date(annee, mois_index, nb_jours) and d2 > date(annee, mois_index,1))):
            # filtrage grossier d'année
            pass
        for j in jours:
            if d1 <= j < d2:
                ic = platform_color(row.get("plateforme"))
                nom = str(row.get("nom_client",""))
                planning[j].append(f"{ic} {nom}")

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

def vue_rapport(df: pd.DataFrame):
    st.title("📊 Rapport")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    c0, c1, c2, c3 = st.columns(4)
    apps = ["Tous"] + sorted([a for a in df["appartement"].dropna().unique().tolist() if a])
    app = c0.selectbox("Appartement", apps)
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    an  = c1.selectbox("Année", annees, index=len(annees)-1) if annees else None
    pfopt = ["Toutes"] + sorted([p for p in df["plateforme"].dropna().unique().tolist() if p])
    pf  = c2.selectbox("Plateforme", pfopt)
    mois_label = c3.selectbox("Mois", ["Tous"] + [f"{i:02d}" for i in range(1,13)])

    data = df.copy()
    if app != "Tous":
        data = data[data["appartement"] == app]
    if an is not None:
        data = data[data["AAAA"] == int(an)]
    if pf != "Toutes":
        data = data[data["plateforme"] == pf]
    if mois_label != "Tous":
        data = data[data["MM"] == int(mois_label)]

    if data.empty:
        st.info("Aucune donnée pour ces filtres.")
        return

    # Détail trié
    detail = data.copy()
    for c in ["date_arrivee","date_depart"]:
        detail[c] = detail[c].apply(format_date_str)
    by = [c for c in ["date_arrivee","appartement","nom_client"] if c in detail.columns]
    detail = detail.sort_values(by=by, na_position="last").reset_index(drop=True)

    cols_detail = [
        "appartement","nom_client","plateforme","telephone",
        "date_arrivee","date_depart","nuitees",
        "brut","commissions","frais_cb","net","menage","taxes_sejour","base","%"
    ]
    cols_detail = [c for c in cols_detail if c in detail.columns]
    st.dataframe(detail[cols_detail], use_container_width=True)

    # Totaux
    t_brut = data["brut"].sum(skipna=True)
    t_net  = data["net"].sum(skipna=True)
    t_base = data["base"].sum(skipna=True)
    t_nuit = data["nuitees"].sum(skipna=True)
    t_pct  = ((data["commissions"].sum()+data["frais_cb"].sum())/data["brut"].sum()*100) if data["brut"].sum() else 0
    st.markdown(totaux_html(t_brut, t_net, t_base, t_nuit, t_pct), unsafe_allow_html=True)

    # Agrégats mensuels (X = 1..12, pas de ligne 0)
    stats = (
        data.groupby(["MM","plateforme"], dropna=True)
            .agg(brut=("brut","sum"),
                 net=("net","sum"),
                 base=("base","sum"),
                 nuitees=("nuitees","sum"))
            .reset_index()
    ).sort_values(["MM","plateforme"]).reset_index(drop=True)

    # Graphes (Streamlit bar_chart avec index ordonné)
    def chart_of(label, col):
        if stats.empty:
            return
        pv = stats.pivot(index="MM", columns="plateforme", values=col).fillna(0)
        pv = pv.reindex(range(1,13)).fillna(0)  # forcer 1..12
        pv.index = [f"{int(m):02d}" for m in pv.index]
        st.markdown(f"**{label}**")
        st.bar_chart(pv)

    chart_of("Brut (€)", "brut")
    chart_of("Net (€)", "net")
    chart_of("Base (€)", "base")
    chart_of("Nuitées", "nuitees")

    # Export détail XLSX
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        detail[cols_detail].to_excel(writer, index=False)
    st.download_button(
        "⬇️ Télécharger le détail (XLSX)",
        data=buf.getvalue(),
        file_name=f"rapport_detail_{app if app!='Tous' else 'all'}_{an}{'' if mois_label=='Tous' else '_'+mois_label}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

def vue_sms(df: pd.DataFrame):
    st.title("✉️ SMS (manuel) + Journal")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    today = date.today()
    demain = today + timedelta(days=1)
    hier = today - timedelta(days=1)

    # Arrivées demain
    st.subheader("📆 Arrivées demain")
    arr = df[df["date_arrivee"] == demain].copy()
    if arr.empty:
        st.info("Aucune arrivée demain.")
    else:
        for idx, r in arr.reset_index(drop=True).iterrows():
            body = sms_message_arrivee(r)
            tel = normalize_tel(r.get("telephone"))
            tel_link = f"tel:{tel}" if tel else ""
            sms_link = f"sms:{tel}?&body={quote(body)}" if tel and body else ""
            st.markdown(f"**{r.get('appartement','')} — {r.get('nom_client','')}** · {r.get('plateforme','')}")
            st.markdown(f"Arrivée: {format_date_str(r.get('date_arrivee'))} • Départ: {format_date_str(r.get('date_depart'))} • Nuitées: {r.get('nuitees','')}")
            st.code(body)
            c1, c2, c3 = st.columns([1,1,2])
            if tel_link: c1.link_button(f"📞 Appeler {tel}", tel_link)
            if sms_link: c2.link_button("📩 Envoyer SMS", sms_link)
            if c3.button("Marquer comme envoyé", key=f"sms_sent_arr_{idx}"):
                # Mettre à jour statut
                mask = (df["appartement"]==r["appartement"]) & (df["nom_client"]==r["nom_client"]) & (df["date_arrivee"]==r["date_arrivee"])
                df.loc[mask, "sms_status"] = "🟢 envoyé"
                sauvegarder_donnees(df)
                st.success("Statut SMS mis à jour → 🟢 envoyé")
                st.rerun()
            st.divider()

    # +24h après départ
    st.subheader("🕒 Relance +24h après départ (départs d’hier)")
    dep_hier = df[df["date_depart"] == hier].copy()
    if dep_hier.empty:
        st.info("Aucun départ hier.")
    else:
        for idx, r in dep_hier.reset_index(drop=True).iterrows():
            body = sms_message_depart(r)
            tel = normalize_tel(r.get("telephone"))
            tel_link = f"tel:{tel}" if tel else ""
            sms_link = f"sms:{tel}?&body={quote(body)}" if tel and body else ""
            st.markdown(f"**{r.get('appartement','')} — {r.get('nom_client','')}** · {r.get('plateforme','')}")
            st.code(body)
            c1, c2, c3 = st.columns([1,1,2])
            if tel_link: c1.link_button(f"📞 Appeler {tel}", tel_link)
            if sms_link: c2.link_button("📩 Envoyer SMS", sms_link)
            if c3.button("Marquer comme envoyé", key=f"sms_sent_dep_{idx}"):
                mask = (df["appartement"]==r["appartement"]) & (df["nom_client"]==r["nom_client"]) & (df["date_arrivee"]==r["date_arrivee"])
                df.loc[mask, "sms_status"] = "🟢 envoyé"
                sauvegarder_donnees(df)
                st.success("Statut SMS mis à jour → 🟢 envoyé")
                st.rerun()
            st.divider()

    # Journal: liste simple
    st.subheader("🗒️ Journal des SMS (statut)")
    show = ensure_schema(df).copy()
    for c in ["date_arrivee","date_depart"]:
        show[c] = show[c].apply(format_date_str)
    st.dataframe(show[["appartement","nom_client","plateforme","telephone","date_arrivee","date_depart","sms_status"]], use_container_width=True)

def vue_export_ics(df: pd.DataFrame):
    st.title("📤 Export ICS (import manuel Google Agenda)")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée à exporter.")
        return

    c0, c1, c2, c3 = st.columns(4)
    apps = ["Tous"] + sorted([a for a in df["appartement"].dropna().unique().tolist() if a])
    app = c0.selectbox("Appartement", apps)
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    an  = c1.selectbox("Année", ["Toutes"] + annees, index=(len(annees) if annees else 0))
    pfopt = ["Toutes"] + sorted([p for p in df["plateforme"].dropna().unique().tolist() if p])
    pf  = c2.selectbox("Plateforme", pfopt)
    mois_label = c3.selectbox("Mois", ["Tous"] + list(range(1,13)))

    data = df.copy()
    if app != "Tous":
        data = data[data["appartement"] == app]
    if an != "Toutes":
        data = data[data["AAAA"] == int(an)]
    if pf != "Toutes":
        data = data[data["plateforme"] == pf]
    if mois_label != "Tous":
        data = data[data["MM"] == int(mois_label)]

    if data.empty:
        st.info("Aucune réservation pour ces filtres.")
        return

    ics_text = df_to_ics(data, cal_name=f"Réservations Multi ({app})" if app!="Tous" else "Réservations Multi")
    st.download_button(
        "⬇️ Télécharger reservations_multi.ics",
        data=ics_text.encode("utf-8"),
        file_name="reservations_multi.ics",
        mime="text/calendar"
    )
    st.caption("Dans Google Agenda : Paramètres → Importer & exporter → Importer → sélectionnez ce fichier .ics.")

# ==============================  NAVIGATION / APP  ==============================

def render_file_section(df: pd.DataFrame):
    st.sidebar.title("📁 Fichier")
    bouton_telecharger(df)
    bouton_restaurer()

def main():
    st.set_page_config(page_title="🏢 Réservations Multi-appartements", layout="wide")

    df = charger_donnees()
    render_file_section(df)
    sidebar_platform_manager(df)

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

    render_cache_section_sidebar()

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