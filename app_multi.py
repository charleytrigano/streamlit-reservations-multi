# app_multi.py — Villa Tobias (multi-appartements) + Journal SMS
# Nouveau : colonne Excel 'sms' (🟠 En attente / 🟢 Envoyé), onglet "🗒️ Journal SMS",
# boutons "Marquer comme envoyé / en attente" qui mettent à jour l'Excel et le journal.

import streamlit as st
import pandas as pd
import numpy as np
import calendar
from datetime import date, timedelta, datetime, timezone
from io import BytesIO
from urllib.parse import quote
import hashlib
import os

FICHIER = "reservations_multi.xlsx"
JOURNAL_FILE = "journal_sms.xlsx"

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

# ==============================  OUTILS / NORMALISATION  ==============================

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
    """Force la lecture du téléphone en TEXTE, retire .0 éventuel, espaces, et garde le +."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = str(x).strip().replace(" ", "")
    if s.endswith(".0"):
        s = s[:-2]
    return s

def ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    base_cols = [
        "appartement",
        "nom_client","plateforme","telephone",
        "date_arrivee","date_depart","nuitees",
        "prix_brut","prix_net","charges","%",
        "AAAA","MM","ical_uid","sms"  # <-- sms ajouté
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=base_cols)

    df = df.copy()

    # Dates -> date
    for c in ["date_arrivee", "date_depart"]:
        if c in df.columns:
            df[c] = df[c].apply(to_date_only)

    # Numériques
    for c in ["prix_brut", "prix_net", "charges", "%"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Calcul charges/% si manquants
    if "prix_brut" in df.columns and "prix_net" in df.columns:
        if "charges" not in df.columns:
            df["charges"] = df["prix_brut"] - df["prix_net"]
        if "%" not in df.columns:
            with pd.option_context("mode.use_inf_as_na", True):
                df["%"] = (df["charges"] / df["prix_brut"] * 100).fillna(0)

    # Arrondis
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

    # Colonnes minimales + sms par défaut
    defaults = {
        "appartement":"Apt A",
        "nom_client":"", "plateforme":"Autre", "telephone":"", "ical_uid":"",
        "sms":"🟠 En attente"
    }
    for k,v in defaults.items():
        if k not in df.columns:
            df[k] = v

    # Téléphone: assure chaîne nettoyée
    if "telephone" in df.columns:
        df["telephone"] = df["telephone"].apply(normalize_tel)

    cols = base_cols
    return df[[c for c in cols if c in df.columns] + [c for c in df.columns if c not in cols]]

def is_total_row(row: pd.Series) -> bool:
    name_is_total = str(row.get("nom_client","")).strip().lower() == "total"
    pf_is_total   = str(row.get("plateforme","")).strip().lower() == "total"
    d1 = row.get("date_arrivee"); d2 = row.get("date_depart")
    no_dates = not isinstance(d1, date) and not isinstance(d2, date)
    has_money = any(pd.notna(row.get(c)) and float(row.get(c) or 0) != 0 for c in ["prix_brut","prix_net","charges"])
    return name_is_total or pf_is_total or (no_dates and has_money)

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

# ==============================  EXCEL I/O  ==============================

@st.cache_data(show_spinner=False)
def _read_excel_cached(path: str, mtime: float):
    return pd.read_excel(path, converters={"telephone": normalize_tel, "appartement": str})

def charger_donnees() -> pd.DataFrame:
    if not os.path.exists(FICHIER):
        return ensure_schema(pd.DataFrame())
    try:
        mtime = os.path.getmtime(FICHIER)
        df = _read_excel_cached(FICHIER, mtime)
        return ensure_schema(df)
    except Exception as e:
        st.error(f"Erreur de lecture Excel : {e}")
        return ensure_schema(pd.DataFrame())

def _force_telephone_text_format_openpyxl(writer, df_to_save: pd.DataFrame, sheet_name: str):
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
    up = st.sidebar.file_uploader("📤 Restauration xlsx (multi)", type=["xlsx"], help="Remplace le fichier actuel")
    if up is not None:
        try:
            df_new = pd.read_excel(up, converters={"telephone": normalize_tel, "appartement": str})
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
        "💾 Sauvegarde xlsx (multi)",
        data=data_xlsx if data_xlsx else b"",
        file_name="reservations_multi.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=(data_xlsx is None),
    )

# ==============================  JOURNAL SMS  ==============================

@st.cache_data(show_spinner=False)
def _read_journal_cached(path: str, mtime: float):
    return pd.read_excel(path)

def charger_journal() -> pd.DataFrame:
    if not os.path.exists(JOURNAL_FILE):
        cols = ["timestamp","action","appartement","nom_client","plateforme","telephone",
                "date_arrivee","date_depart","nuitees","message","statut"]
        return pd.DataFrame(columns=cols)
    try:
        mtime = os.path.getmtime(JOURNAL_FILE)
        df = _read_journal_cached(JOURNAL_FILE, mtime)
        return df
    except Exception:
        return pd.DataFrame(columns=["timestamp","action","appartement","nom_client","plateforme","telephone",
                                     "date_arrivee","date_depart","nuitees","message","statut"])

def journal_append(action: str, r: pd.Series, message: str, statut: str):
    j = charger_journal()
    row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "action": action,  # "arrivee", "relance", "manuel"
        "appartement": str(r.get("appartement","")),
        "nom_client": str(r.get("nom_client","")),
        "plateforme": str(r.get("plateforme","")),
        "telephone": normalize_tel(r.get("telephone")),
        "date_arrivee": format_date_str(r.get("date_arrivee")) if isinstance(r.get("date_arrivee"), date) else "",
        "date_depart":  format_date_str(r.get("date_depart"))  if isinstance(r.get("date_depart"),  date) else "",
        "nuitees": int(r.get("nuitees") or 0),
        "message": message,
        "statut": statut,  # "envoye" / "attente"
    }
    j = pd.concat([j, pd.DataFrame([row])], ignore_index=True)
    try:
        with pd.ExcelWriter(JOURNAL_FILE, engine="openpyxl") as w:
            j.to_excel(w, index=False)
        st.cache_data.clear()
    except Exception as e:
        st.warning(f"Journal non sauvegardé: {e}")

def vue_journal_sms():
    st.title("🗒️ Journal des SMS")
    j = charger_journal()
    if j.empty:
        st.info("Aucun enregistrement de SMS pour le moment.")
        return

    # Filtres
    c1, c2, c3 = st.columns(3)
    act = c1.selectbox("Action", ["Toutes"] + sorted(j["action"].dropna().unique().tolist()))
    stat = c2.selectbox("Statut", ["Tous"] + sorted(j["statut"].dropna().unique().tolist()))
    cli = c3.text_input("Recherche client / tel / appart")

    df = j.copy()
    if act != "Toutes":
        df = df[df["action"] == act]
    if stat != "Tous":
        df = df[df["statut"] == stat]
    if cli.strip():
        q = cli.strip().lower()
        df = df[df.astype(str).apply(lambda row: q in " ".join(row.str.lower()), axis=1)]

    st.dataframe(df, use_container_width=True)

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False)
    st.download_button(
        "⬇️ Télécharger le journal (XLSX)",
        data=buf.getvalue(),
        file_name="journal_sms.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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

def _stable_uid(appartement, nom_client, plateforme, d1, d2, tel, salt="v1"):
    base = f"{appartement}|{nom_client}|{plateforme}|{d1}|{d2}|{tel}|{salt}"
    h = hashlib.sha1(base.encode("utf-8")).hexdigest()
    return f"vt-{h}@villatobias"

def df_to_ics(df: pd.DataFrame, cal_name: str = "Villa Tobias – Réservations") -> str:
    df = ensure_schema(df)
    if df.empty:
        return (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PROID:-//Villa Tobias//Reservations//FR\r\n"
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
    A("PRODID:-//Villa Tobias//Reservations//FR")
    A(f"X-WR-CALNAME:{_ics_escape(cal_name)}")
    A("CALSCALE:GREGORIAN")
    A("METHOD:PUBLISH")

    for _, row in core.iterrows():
        d1 = row.get("date_arrivee")
        d2 = row.get("date_depart")
        if not (isinstance(d1, date) and isinstance(d2, date)):
            continue

        appartement = str(row.get("appartement") or "").strip()
        plateforme  = str(row.get("plateforme") or "").strip()
        nom_client  = str(row.get("nom_client") or "").strip()
        tel         = str(row.get("telephone") or "").strip()

        summary = " - ".join([x for x in [appartement, plateforme, nom_client, tel] if x])
        brut = float(row.get("prix_brut") or 0)
        net  = float(row.get("prix_net") or 0)
        nuitees = int(row.get("nuitees") or ((d2 - d1).days))

        desc = (
            f"Appartement: {appartement}\\n"
            f"Plateforme: {plateforme}\\n"
            f"Client: {nom_client}\\n"
            f"Téléphone: {tel}\\n"
            f"Arrivee: {d1.strftime('%Y/%m/%d')}\\n"
            f"Depart: {d2.strftime('%Y/%m/%d')}\\n"
            f"Nuitees: {nuitees}\\n"
            f"Brut: {brut:.2f} €\\nNet: {net:.2f} €"
        )

        uid_existing = str(row.get("ical_uid") or "").strip()
        uid = uid_existing if uid_existing else _stable_uid(appartement, nom_client, plateforme, d1, d2, tel)

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

# ==============================  TEMPLATES SMS ==============================

def sms_message_arrivee(row: pd.Series) -> str:
    d1 = row.get("date_arrivee")
    d2 = row.get("date_depart")
    d1s = d1.strftime("%Y/%m/%d") if isinstance(d1, date) else ""
    d2s = d2.strftime("%Y/%m/%d") if isinstance(d2, date) else ""
    nuitees = int(row.get("nuitees") or ((d2 - d1).days if isinstance(d1, date) and isinstance(d2, date) else 0))
    plateforme = str(row.get("plateforme") or "")
    nom = str(row.get("nom_client") or "")
    tel_aff = str(row.get("telephone") or "").strip()

    return (
        "VILLA TOBIAS\n"
        f"Plateforme : {plateforme}\n"
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

# ==============================  UI : TOTAUX  ==============================

def _totaux_chips_html(total_brut, total_net, total_chg, total_nuits, pct_moy):
    return f"""
<style>
.chips-wrap {{ display:flex; flex-wrap:wrap; gap:12px; margin:8px 0 16px 0; }}
.chip {{ padding:10px 12px; border-radius:10px; background: rgba(127,127,127,0.12); border: 1px solid rgba(127,127,127,0.25); }}
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

# ==============================  VUES  ==============================

def _filters_bar(df: pd.DataFrame, show_pf=True, show_year=True, show_month=True, show_app=True, key="f"):
    n = (1 if show_app else 0) + (1 if show_pf else 0) + (1 if show_year else 0) + (1 if show_month else 0)
    if n == 0: n = 1
    cols = st.columns(n)

    i = 0
    val_app, val_pf, val_year, val_month = None, None, None, None
    if show_app:
        apps = sorted(df["appartement"].dropna().astype(str).unique().tolist())
        val_app = cols[i].selectbox("Appartement", ["Tous"] + apps, key=f"{key}_app"); i+=1
    if show_pf:
        pfs = sorted(df["plateforme"].dropna().astype(str).unique().tolist())
        val_pf = cols[i].selectbox("Plateforme", ["Toutes"] + pfs, key=f"{key}_pf"); i+=1
    if show_year:
        years = sorted([int(x) for x in df["AAAA"].dropna().unique().tolist()])
        val_year = cols[i].selectbox("Année", ["Toutes"] + years, key=f"{key}_year"); i+=1
    if show_month:
        val_month = cols[i].selectbox("Mois", ["Tous"] + [f"{m:02d}" for m in range(1,13)], key=f"{key}_mois"); i+=1

    data = df.copy()
    if show_app and val_app != "Tous":
        data = data[data["appartement"] == val_app]
    if show_pf and val_pf != "Toutes":
        data = data[data["plateforme"] == val_pf]
    if show_year and val_year != "Toutes":
        data = data[data["AAAA"] == int(val_year)]
    if show_month and val_month != "Tous":
        data = data[data["MM"] == int(val_month)]
    return data, {"appartement": val_app, "plateforme": val_pf, "annee": val_year, "mois": val_month}

def vue_reservations(df: pd.DataFrame):
    st.title("🏠 Réservations (multi)")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    data, _ = _filters_bar(df, show_app=True, show_pf=True, show_year=True, show_month=True, key="res")
    core, totals = split_totals(data)
    core = sort_core(core)

    # Totaux (sur le filtré)
    if not core.empty:
        total_brut   = core["prix_brut"].sum(skipna=True)
        total_net    = core["prix_net"].sum(skipna=True)
        total_chg    = core["charges"].sum(skipna=True)
        total_nuits  = core["nuitees"].sum(skipna=True)
        pct_moy = (core["charges"].sum() / core["prix_brut"].sum() * 100) if core["prix_brut"].sum() else 0
        st.markdown(_totaux_chips_html(total_brut, total_net, total_chg, total_nuits, pct_moy), unsafe_allow_html=True)

    show = pd.concat([core, totals], ignore_index=True)
    for c in ["date_arrivee","date_depart"]:
        if c in show.columns:
            show[c] = show[c].apply(format_date_str)
    st.dataframe(show, use_container_width=True)

def vue_ajouter(df: pd.DataFrame):
    st.title("➕ Ajouter une réservation")
    st.caption("Saisie compacte (libellés inline)")

    def inline_input(label, widget_fn, key=None, **widget_kwargs):
        c1, c2 = st.columns([1,2])
        with c1: st.markdown(f"**{label}**")
        with c2: return widget_fn(label, key=key, label_visibility="collapsed", **widget_kwargs)

    apps = sorted(ensure_schema(df)["appartement"].dropna().astype(str).unique().tolist())
    app_sel = inline_input("Appartement", st.selectbox, key="add_app", options=(apps + ["(nouveau…)"]) if apps else ["Apt A","(nouveau…)"])
    app_new = ""
    if app_sel == "(nouveau…)":
        app_new = inline_input("Nom nouvel appart.", st.text_input, key="add_app_new", value="")
    appartement = app_new.strip() if app_new.strip() else app_sel

    nom = inline_input("Nom", st.text_input, key="add_nom", value="")
    tel = inline_input("Téléphone (+33...)", st.text_input, key="add_tel", value="")
    plateforme = inline_input("Plateforme", st.selectbox, key="add_pf",
                              options=["Booking","Airbnb","Autre"], index=0)

    arrivee = inline_input("Arrivée", st.date_input, key="add_arrivee", value=date.today())
    min_dep = arrivee + timedelta(days=1)
    depart  = inline_input("Départ",  st.date_input, key="add_depart",
                           value=min_dep, min_value=min_dep)

    brut = inline_input("Prix brut (€)", st.number_input, key="add_brut",
                        min_value=0.0, step=1.0, format="%.2f")
    net  = inline_input("Prix net (€)",  st.number_input, key="add_net",
                        min_value=0.0, step=1.0, format="%.2f")

    charges_calc = max(float(brut) - float(net), 0.0)
    pct_calc = (charges_calc / float(brut) * 100) if float(brut) > 0 else 0.0

    inline_input("Charges (€)", st.number_input, key="add_ch",
                 value=round(charges_calc,2), step=0.01, format="%.2f", disabled=True)
    inline_input("Commission (%)", st.number_input, key="add_pct",
                 value=round(pct_calc,2), step=0.01, format="%.2f", disabled=True)

    ok = st.button("Enregistrer")
    if ok:
        if not appartement or appartement == "(nouveau…)":
            st.error("Veuillez saisir un nom d'appartement.")
            return
        if net > brut:
            st.error("Le prix net ne peut pas être supérieur au prix brut.")
            return
        if depart < arrivee + timedelta(days=1):
            st.error("La date de départ doit être au moins le lendemain de l’arrivée.")
            return

        ligne = {
            "appartement": appartement,
            "nom_client": (nom or "").strip(),
            "plateforme": plateforme,
            "telephone": normalize_tel(tel),
            "date_arrivee": arrivee,
            "date_depart": depart,
            "prix_brut": float(brut),
            "prix_net": float(net),
            "charges": round(charges_calc, 2),
            "%": round(pct_calc, 2),
            "nuitees": (depart - arrivee).days,
            "AAAA": arrivee.year,
            "MM": arrivee.month,
            "ical_uid": "",
            "sms": "🟠 En attente"  # par défaut
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

    cols = st.columns(2)
    appartement = cols[0].text_input("Appartement", df.at[i, "appartement"])
    plateforme  = cols[1].selectbox("Plateforme", ["Booking","Airbnb","Autre"],
                     index = ["Booking","Airbnb","Autre"].index(df.at[i,"plateforme"]) if df.at[i,"plateforme"] in ["Booking","Airbnb","Autre"] else 2)

    c2 = st.columns(3)
    nom = c2[0].text_input("Nom", df.at[i, "nom_client"])
    tel = c2[1].text_input("Téléphone", normalize_tel(df.at[i, "telephone"]))
    sms_stat = c2[2].selectbox("Statut SMS", ["🟠 En attente","🟢 Envoyé"],
                               index=0 if df.at[i,"sms"]!="🟢 Envoyé" else 1)

    arrivee = st.date_input("Arrivée", df.at[i,"date_arrivee"] if isinstance(df.at[i,"date_arrivee"], date) else date.today())
    depart  = st.date_input("Départ",  df.at[i,"date_depart"] if isinstance(df.at[i,"date_depart"], date) else arrivee + timedelta(days=1), min_value=arrivee+timedelta(days=1))

    c = st.columns(3)
    brut = c[0].number_input("Prix brut (€)", min_value=0.0, value=float(df.at[i,"prix_brut"]) if pd.notna(df.at[i,"prix_brut"]) else 0.0, step=1.0, format="%.2f")
    net  = c[1].number_input("Prix net (€)",  min_value=0.0, value=float(df.at[i,"prix_net"]) if pd.notna(df.at[i,"prix_net"]) else 0.0, step=1.0, format="%.2f")
    charges_calc = max(brut - net, 0.0)
    pct_calc = (charges_calc / brut * 100) if brut > 0 else 0.0
    c[2].markdown(f"**Charges**: {charges_calc:.2f} €  \n**%**: {pct_calc:.2f}")

    c1, c2 = st.columns(2)
    if c1.button("💾 Enregistrer"):
        if depart < arrivee + timedelta(days=1):
            st.error("La date de départ doit être au moins le lendemain de l’arrivée.")
            return
        df.at[i,"appartement"]  = appartement.strip() or "Apt A"
        df.at[i,"nom_client"]   = nom.strip()
        df.at[i,"plateforme"]   = plateforme
        df.at[i,"telephone"]    = normalize_tel(tel)
        df.at[i,"date_arrivee"] = arrivee
        df.at[i,"date_depart"]  = depart
        df.at[i,"prix_brut"]    = float(brut)
        df.at[i,"prix_net"]     = float(net)
        df.at[i,"charges"]      = round(charges_calc, 2)
        df.at[i,"%"]            = round(pct_calc, 2)
        df.at[i,"nuitees"]      = (depart - arrivee).days
        df.at[i,"AAAA"]         = arrivee.year
        df.at[i,"MM"]           = arrivee.month
        df.at[i,"sms"]          = sms_stat
        df.drop(columns=["identifiant"], inplace=True, errors="ignore")
        sauvegarder_donnees(df)
        st.success("✅ Modifié")
        st.rerun()

    if c2.button("🗑 Supprimer"):
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

    c1, c2, c3 = st.columns(3)
    apps = sorted(df["appartement"].dropna().astype(str).unique().tolist())
    app_sel = c1.selectbox("Appartement", ["Tous"] + apps)
    mois_nom = c2.selectbox("Mois", list(calendar.month_name)[1:], index=max(0, date.today().month-1))
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    if not annees:
        st.warning("Aucune année disponible.")
        return
    annee = c3.selectbox("Année", annees, index=len(annees)-1)

    data = df.copy()
    if app_sel != "Tous":
        data = data[data["appartement"] == app_sel]

    mois_index = list(calendar.month_name).index(mois_nom)
    nb_jours = calendar.monthrange(annee, mois_index)[1]
    jours = [date(annee, mois_index, j+1) for j in range(nb_jours)]
    planning = {j: [] for j in jours}
    couleurs = {"Booking":"🟦","Airbnb":"🟩","Autre":"🟧"}

    core, _ = split_totals(data)
    for _, row in core.iterrows():
        d1 = row["date_arrivee"]; d2 = row["date_depart"]
        if not (isinstance(d1, date) and isinstance(d2, date)):
            continue
        for j in jours:
            if d1 <= j < d2:
                ic = couleurs.get(row["plateforme"], "⬜")
                planning[j].append(f"{ic} {row['nom_client']}")

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
    st.title("📊 Rapport (par mois et plateforme)")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    data, _ = _filters_bar(df, show_app=True, show_pf=True, show_year=True, show_month=True, key="rap")
    if data.empty:
        st.info("Aucune donnée pour ces filtres.")
        return

    # Détail + totaux
    detail = data.copy()
    for c in ["date_arrivee","date_depart"]:
        detail[c] = detail[c].apply(format_date_str)
    by = [c for c in ["appartement","date_arrivee","nom_client"] if c in detail.columns]
    if by:
        detail = detail.sort_values(by=by, na_position="last").reset_index(drop=True)

    cols_detail = [
        "appartement","nom_client","plateforme","telephone",
        "date_arrivee","date_depart","nuitees",
        "prix_brut","prix_net","charges","%","sms"
    ]
    cols_detail = [c for c in cols_detail if c in detail.columns]
    st.dataframe(detail[cols_detail], use_container_width=True)

    total_brut   = data["prix_brut"].sum(skipna=True)
    total_net    = data["prix_net"].sum(skipna=True)
    total_chg    = data["charges"].sum(skipna=True)
    total_nuits  = data["nuitees"].sum(skipna=True)
    pct_moy = (data["charges"].sum() / data["prix_brut"].sum() * 100) if data["prix_brut"].sum() else 0
    st.markdown(_totaux_chips_html(total_brut, total_net, total_chg, total_nuits, pct_moy), unsafe_allow_html=True)

    stats = (
        data.groupby(["MM","plateforme"], dropna=True)
            .agg(prix_brut=("prix_brut","sum"),
                 prix_net=("prix_net","sum"),
                 charges=("charges","sum"),
                 nuitees=("nuitees","sum"))
            .reset_index()
    )
    stats = stats.sort_values(["MM","plateforme"]).reset_index(drop=True)
    if stats.empty:
        st.info("Aucune donnée agrégée.")
        return

    def chart_of(metric_label, metric_col):
        pivot = stats.pivot(index="MM", columns="plateforme", values=metric_col).fillna(0.0)
        pivot = pivot.sort_index()
        pivot.index = [f"{int(m):02d}" for m in pivot.index]
        st.markdown(f"**{metric_label}**")
        st.bar_chart(pivot)

    chart_of("Revenus bruts", "prix_brut")
    chart_of("Revenus nets", "prix_net")
    chart_of("Charges", "charges")
    chart_of("Nuitées", "nuitees")

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        detail[cols_detail].to_excel(writer, index=False)
    st.download_button(
        "⬇️ Télécharger le détail (XLSX)",
        data=buf.getvalue(),
        file_name="rapport_detail_filtre.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

def vue_clients(df: pd.DataFrame):
    st.title("👥 Liste des clients")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    data, _ = _filters_bar(df, show_app=True, show_pf=False, show_year=True, show_month=True, key="cli")
    if data.empty:
        st.info("Aucune donnée pour ces filtres.")
        return

    data["prix_brut/nuit"] = data.apply(lambda r: round((r["prix_brut"]/r["nuitees"]) if r["nuitees"] else 0,2), axis=1)
    data["prix_net/nuit"]  = data.apply(lambda r: round((r["prix_net"]/r["nuitees"])  if r["nuitees"] else 0,2), axis=1)

    show = data.copy()
    for c in ["date_arrivee","date_depart"]:
        show[c] = show[c].apply(format_date_str)

    cols = ["appartement","nom_client","plateforme","telephone","date_arrivee","date_depart",
            "nuitees","prix_brut","prix_net","charges","%","sms","prix_brut/nuit","prix_net/nuit"]
    cols = [c for c in cols if c in show.columns]
    st.dataframe(show[cols], use_container_width=True)
    st.download_button(
        "📥 Télécharger (CSV)",
        data=show[cols].to_csv(index=False).encode("utf-8"),
        file_name="liste_clients.csv",
        mime="text/csv"
    )

# ==============================  SMS (MANUEL) ==============================

def _update_sms_status(df: pd.DataFrame, row_index, status_emoji: str):
    """Met à jour la colonne 'sms' (🟢 ou 🟠) pour l'index donné et sauvegarde."""
    if "sms" not in df.columns:
        df["sms"] = "🟠 En attente"
    df.at[row_index, "sms"] = status_emoji
    sauvegarder_donnees(df)

def vue_sms(df: pd.DataFrame):
    st.title("✉️ SMS (envoi manuel)")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    # Filtre Appartement (utile si plusieurs)
    apps = sorted(df["appartement"].dropna().astype(str).unique().tolist())
    app_sel = st.selectbox("Appartement", ["Tous"] + apps, key="sms_app")
    data = df if app_sel == "Tous" else df[df["appartement"] == app_sel]

    today = date.today()
    demain = today + timedelta(days=1)
    hier = today - timedelta(days=1)

    colA, colB = st.columns(2)

    # --- Arrivées demain ---
    with colA:
        st.subheader("📆 Arrivées demain")
        arrives = data[data["date_arrivee"] == demain]
        if arrives.empty:
            st.info("Aucune arrivée demain.")
        else:
            for idx, r in arrives.iterrows():  # conserve l'index d'origine
                body = sms_message_arrivee(r)
                tel = normalize_tel(r.get("telephone"))
                tel_link = f"tel:{tel}" if tel else ""
                sms_link = f"sms:{tel}?&body={quote(body)}" if tel and body else ""

                st.markdown(f"**{r.get('nom_client','')}** — {r.get('plateforme','')} — {r.get('appartement','')}")
                st.markdown(f"Arrivée: {format_date_str(r.get('date_arrivee'))} • "
                            f"Départ: {format_date_str(r.get('date_depart'))} • "
                            f"Nuitées: {r.get('nuitees','')}")
                st.code(body)

                c1, c2, c3, c4 = st.columns([1,1,1,2])
                if tel_link:
                    c1.link_button("📞 Appeler", tel_link)
                if sms_link:
                    c2.link_button("📩 SMS", sms_link)
                if c3.button("✅ Marquer envoyé", key=f"mark_send_arr_{idx}"):
                    _update_sms_status(df, idx, "🟢 Envoyé")
                    journal_append("arrivee", r, body, "envoye")
                    st.success("Marqué comme envoyé.")
                    st.rerun()
                if c4.button("🕒 Marquer en attente", key=f"mark_wait_arr_{idx}"):
                    _update_sms_status(df, idx, "🟠 En attente")
                    journal_append("arrivee", r, body, "attente")
                    st.info("Marqué en attente.")
                    st.rerun()
                st.divider()

    # --- Relance +24h après départ ---
    with colB:
        st.subheader("🕒 Relance +24h après départ")
        dep_24h = data[data["date_depart"] == hier]
        if dep_24h.empty:
            st.info("Aucun départ hier.")
        else:
            for idx, r in dep_24h.iterrows():
                body = sms_message_depart(r)
                tel = normalize_tel(r.get("telephone"))
                tel_link = f"tel:{tel}" if tel else ""
                sms_link = f"sms:{tel}?&body={quote(body)}" if tel and body else ""

                st.markdown(f"**{r.get('nom_client','')}** — {r.get('plateforme','')} — {r.get('appartement','')}")
                st.code(body)

                c1, c2, c3, c4 = st.columns([1,1,1,2])
                if tel_link:
                    c1.link_button("📞 Appeler", tel_link)
                if sms_link:
                    c2.link_button("📩 SMS", sms_link)
                if c3.button("✅ Marquer envoyé", key=f"mark_send_dep_{idx}"):
                    _update_sms_status(df, idx, "🟢 Envoyé")
                    journal_append("relance", r, body, "envoye")
                    st.success("Marqué comme envoyé.")
                    st.rerun()
                if c4.button("🕒 Marquer en attente", key=f"mark_wait_dep_{idx}"):
                    _update_sms_status(df, idx, "🟠 En attente")
                    journal_append("relance", r, body, "attente")
                    st.info("Marqué en attente.")
                    st.rerun()
                st.divider()

    # --- Composeur manuel ---
    st.subheader("✍️ Composer un SMS manuel")
    df_pick = data.copy()
    df_pick["id_aff"] = (
        df_pick["appartement"].astype(str) + " | " +
        df_pick["nom_client"].astype(str) + " | " +
        df_pick["plateforme"].astype(str) + " | " +
        df_pick["date_arrivee"].apply(format_date_str)
    )
    if df_pick.empty:
        st.info("Aucune réservation pour composer un message.")
        return

    choix = st.selectbox("Choisir une réservation", df_pick["id_aff"])
    sel = df_pick[df_pick["id_aff"] == choix]
    if sel.empty:
        st.info("Sélection invalide.")
        return
    idx = sel.index[0]
    r = sel.iloc[0]
    tel = normalize_tel(r.get("telephone"))

    choix_type = st.radio("Modèle de message",
                          ["Arrivée (demande d’heure)","Relance après départ","Message libre"],
                          horizontal=True)
    if choix_type == "Arrivée (demande d’heure)":
        body = sms_message_arrivee(r)
        action = "arrivee"
    elif choix_type == "Relance après départ":
        body = sms_message_depart(r)
        action = "relance"
    else:
        body = st.text_area("Votre message", value="", height=160, placeholder="Tapez votre SMS ici…")
        action = "manuel"

    c1, c2, c3, c4 = st.columns([2,1,1,1])
    with c1:
        st.code(body or "—")
    if tel and body:
        c2.link_button("📞 Appeler", f"tel:{tel}")
        c3.link_button("📩 SMS", f"sms:{tel}?&body={quote(body)}")
    else:
        c2.write("—")
        c3.write("—")

    if c4.button("✅ Marquer envoyé", key="sms_manual_mark_send"):
        _update_sms_status(df, idx, "🟢 Envoyé")
        journal_append(action, r, body, "envoye")
        st.success("Marqué comme envoyé.")
        st.rerun()
    st.caption("Astuce : utilisez les boutons de marquage pour tenir à jour la colonne 'sms' et le journal.")

# ==============================  APP  ==============================

def main():
    st.set_page_config(page_title="📖 Réservations Multi – Villa Tobias", layout="wide")

    st.sidebar.title("📁 Fichier")
    df_tmp = charger_donnees()
    bouton_telecharger(df_tmp)
    bouton_restaurer()

    st.sidebar.title("🧭 Navigation")
    onglet = st.sidebar.radio(
        "Aller à",
        ["🏠 Réservations","➕ Ajouter","✏️ Modifier / Supprimer",
         "📅 Calendrier","📊 Rapport","👥 Liste clients","📤 Export ICS","✉️ SMS","🗒️ Journal SMS"]
    )

    render_cache_section_sidebar()
    df = charger_donnees()

    if onglet == "🏠 Réservations":
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
    elif onglet == "📤 Export ICS":
        # filtres et export ICS sur les données courantes
        data, _ = _filters_bar(df, show_app=True, show_pf=True, show_year=True, show_month=True, key="ics")
        if data.empty:
            st.info("Aucune réservation pour ces filtres.")
        else:
            ics_text = df_to_ics(data)
            st.download_button(
                "⬇️ Télécharger reservations.ics",
                data=ics_text.encode("utf-8"),
                file_name="reservations.ics",
                mime="text/calendar"
            )
            st.caption("Dans Google Agenda : Paramètres → Importer & exporter → Importer → sélectionnez ce fichier .ics.")
    elif onglet == "✉️ SMS":
        vue_sms(df)
    elif onglet == "🗒️ Journal SMS":
        vue_journal_sms()

if __name__ == "__main__":
    main()