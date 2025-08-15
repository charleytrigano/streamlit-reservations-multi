# app_multi.py — Réservations Multi-Appartements (COMPLET + 👥 Liste clients)
# Fichier Excel attendu: reservations_multi.xlsx (feuilles "Réservations" et "Plateformes")

import streamlit as st
import pandas as pd
import numpy as np
import calendar
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import quote
import hashlib
import os

FICHIER_XLSX = "reservations_multi.xlsx"
SMS_LOG = "sms_log.csv"

# ============================== Utils (dates / tel / formats) ==============================

def to_date_only(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    try:
        return pd.to_datetime(x).date()
    except Exception:
        return None

def fmt_day(d):
    return d.strftime("%Y/%m/%d") if isinstance(d, date) else ""

def normalize_tel(x: object) -> str:
    """Lecture en texte : supprime espaces, garde +, supprime .0 éventuel."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = str(x).strip().replace(" ", "")
    if s.endswith(".0"):
        s = s[:-2]
    return s

# ============================== Lecture / écriture Excel ==============================

@st.cache_data(show_spinner=False)
def _read_excel_cached(path: str, mtime: float):
    # Force la colonne telephone en texte
    xls = pd.read_excel(path, sheet_name=None, converters={"telephone": normalize_tel})
    return xls

def charger_fichier():
    if not os.path.exists(FICHIER_XLSX):
        return {"Réservations": pd.DataFrame(), "Plateformes": pd.DataFrame()}
    try:
        mtime = os.path.getmtime(FICHIER_XLSX)
        return _read_excel_cached(FICHIER_XLSX, mtime)
    except Exception as e:
        st.error(f"Erreur lecture Excel: {e}")
        return {"Réservations": pd.DataFrame(), "Plateformes": pd.DataFrame()}

def ensure_schema_resa(df: pd.DataFrame) -> pd.DataFrame:
    base_cols = [
        "appartement","nom_client","plateforme","telephone",
        "date_arrivee","date_depart","nuitees",
        # Modèle multi
        "brut","commissions","frais_cb","net","menage","taxes_sejour","base",
        "%commission","AAAA","MM","ical_uid","sms_status"
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=base_cols)

    df = df.copy()

    # Dates
    for c in ["date_arrivee","date_depart"]:
        if c in df.columns:
            df[c] = df[c].apply(to_date_only)

    # Numériques
    for c in ["brut","commissions","frais_cb","net","menage","taxes_sejour","base","%commission","nuitees"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Recalculs sûrs (modèle multi)
    # net = brut - commissions - frais_cb
    if {"brut","commissions","frais_cb"}.issubset(df.columns):
        df["net"] = (df["brut"] - df["commissions"] - df["frais_cb"]).round(2)

    # base = net - menage - taxes_sejour
    if {"net","menage","taxes_sejour"}.issubset(df.columns):
        df["base"] = (df["net"] - df["menage"] - df["taxes_sejour"]).round(2)

    # %commission = (commissions + frais_cb) / brut * 100
    if {"commissions","frais_cb","brut"}.issubset(df.columns):
        with pd.option_context("mode.use_inf_as_na", True):
            df["%commission"] = (((df["commissions"] + df["frais_cb"]) / df["brut"]) * 100)\
                                    .replace([np.inf,-np.inf], np.nan).fillna(0).round(2)

    # Nuitées
    if {"date_arrivee","date_depart"}.issubset(df.columns):
        df["nuitees"] = [
            (d2 - d1).days if (isinstance(d1, date) and isinstance(d2, date)) else np.nan
            for d1, d2 in zip(df["date_arrivee"], df["date_depart"])
        ]

    # AAAA / MM
    if "date_arrivee" in df.columns:
        df["AAAA"] = df["date_arrivee"].apply(lambda d: d.year if isinstance(d, date) else np.nan).astype("Int64")
        df["MM"]   = df["date_arrivee"].apply(lambda d: d.month if isinstance(d, date) else np.nan).astype("Int64")

    # Défauts
    defaults = {
        "appartement":"", "nom_client":"", "plateforme":"Autre", "telephone":"",
        "ical_uid":"", "sms_status":"🟠"
    }
    for k,v in defaults.items():
        if k not in df.columns:
            df[k] = v

    # Tél
    df["telephone"] = df["telephone"].apply(normalize_tel)

    # Ordre colonnes
    cols = [c for c in base_cols if c in df.columns] + [c for c in df.columns if c not in base_cols]
    return df[cols]

def ensure_schema_plateformes(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame({"plateforme":["Booking","Airbnb","Autre"], "couleur_hex":["#1f77b4","#2ca02c","#ff7f0e"]})
    if "plateforme" not in df.columns:
        df["plateforme"] = ""
    if "couleur_hex" not in df.columns:
        df["couleur_hex"] = "#999999"
    return df[["plateforme","couleur_hex"]]

def sauvegarder_fichier(df_resa: pd.DataFrame, df_plats: pd.DataFrame):
    df_resa = ensure_schema_resa(df_resa)
    df_plats = ensure_schema_plateformes(df_plats)
    try:
        with pd.ExcelWriter(FICHIER_XLSX, engine="openpyxl") as w:
            df_resa.to_excel(w, index=False, sheet_name="Réservations")
            df_plats.to_excel(w, index=False, sheet_name="Plateformes")
        st.cache_data.clear()
        st.success("💾 Sauvegardé")
    except Exception as e:
        st.error(f"Échec sauvegarde: {e}")

def telecharger_fichier(df_resa, df_plats):
    buf = BytesIO()
    try:
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            ensure_schema_resa(df_resa).to_excel(w, index=False, sheet_name="Réservations")
            ensure_schema_plateformes(df_plats).to_excel(w, index=False, sheet_name="Plateformes")
        data = buf.getvalue()
    except Exception as e:
        st.sidebar.error(f"Export indisponible: {e}")
        data = None
    st.sidebar.download_button(
        "💾 Sauvegarde xlsx",
        data=data if data else b"",
        file_name="reservations_multi.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=(data is None)
    )

def restaurer_fichier():
    up = st.sidebar.file_uploader("📤 Restauration xlsx", type=["xlsx"])
    if up is not None:
        try:
            xls = pd.read_excel(up, sheet_name=None, converters={"telephone": normalize_tel})
            df_resa = ensure_schema_resa(xls.get("Réservations", pd.DataFrame()))
            df_plat = ensure_schema_plateformes(xls.get("Plateformes", pd.DataFrame()))
            sauvegarder_fichier(df_resa, df_plat)
            st.sidebar.success("✅ Restauré")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Erreur import: {e}")

# ============================== Totaux (chips) ==============================

def chips_totaux(df: pd.DataFrame):
    if df.empty:
        return
    total_brut = df["brut"].sum(skipna=True)
    total_net  = df["net"].sum(skipna=True)
    total_base = df["base"].sum(skipna=True)
    total_nuit = df["nuitees"].sum(skipna=True)

    # %commission moyen pondéré sur brut > 0
    brut_pos = df["brut"].where(df["brut"] > 0).sum()
    pct_moy = (((df["commissions"] + df["frais_cb"]).sum() / brut_pos) * 100) if brut_pos else 0

    html = f"""
    <style>
      .chips {{display:flex; flex-wrap:wrap; gap:10px; margin:6px 0 12px 0}}
      .chip {{padding:10px 12px; border-radius:10px; background:rgba(127,127,127,.12); border:1px solid rgba(127,127,127,.25)}}
      .chip b {{display:block; margin-bottom:4px}}
    </style>
    <div class="chips">
      <div class="chip"><b>Total Montant (brut)</b><div>{total_brut:,.2f} €</div></div>
      <div class="chip"><b>Total Montant (net)</b><div>{total_net:,.2f} €</div></div>
      <div class="chip"><b>Total Base</b><div>{total_base:,.2f} €</div></div>
      <div class="chip"><b>Total Nuitées</b><div>{int(total_nuit) if pd.notna(total_nuit) else 0}</div></div>
      <div class="chip"><b>Commission moy.</b><div>{pct_moy:.2f} %</div></div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

# ============================== ICS Export ==============================

def _ics_escape(s: str) -> str:
    if s is None: return ""
    s = str(s)
    s = s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    return s

def _dt_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def _date_to_ics(d: date) -> str:
    return d.strftime("%Y%m%d")

def _stable_uid(row) -> str:
    base = f"{row.get('appartement')}|{row.get('plateforme')}|{row.get('nom_client')}|{row.get('date_arrivee')}|{row.get('date_depart')}|{row.get('telephone')}"
    return f"multi-{hashlib.sha1(base.encode()).hexdigest()}@villatobias"

def df_to_ics(df: pd.DataFrame, cal_name="Multi – Réservations"):
    df = ensure_schema_resa(df)
    core = df.copy()
    core = core[(core["date_arrivee"].notna()) & (core["date_depart"].notna())]
    lines = []
    A = lines.append
    A("BEGIN:VCALENDAR")
    A("VERSION:2.0")
    A("PRODID:-//Villa Tobias//Multi//FR")
    A(f"X-WR-CALNAME:{_ics_escape(cal_name)}")
    A("CALSCALE:GREGORIAN")
    A("METHOD:PUBLISH")
    for _, r in core.iterrows():
        d1 = r["date_arrivee"]; d2 = r["date_depart"]
        if not (isinstance(d1, date) and isinstance(d2, date)):
            continue
        summary = " - ".join([str(r.get("appartement","")).strip(),
                              str(r.get("plateforme","")).strip(),
                              str(r.get("nom_client","")).strip(),
                              normalize_tel(r.get("telephone"))]).strip(" -")
        desc = (
            f"Appartement: {r.get('appartement','')}\\n"
            f"Plateforme: {r.get('plateforme','')}\\n"
            f"Client: {r.get('nom_client','')}\\n"
            f"Téléphone: {normalize_tel(r.get('telephone'))}\\n"
            f"Arrivée: {fmt_day(d1)}\\n"
            f"Départ: {fmt_day(d2)}\\n"
            f"Nuitées: {int(r.get('nuitees') or (d2-d1).days)}\\n"
            f"Brut: {float(r.get('brut') or 0):.2f} €\\nNet: {float(r.get('net') or 0):.2f} €"
        )
        uid = str(r.get("ical_uid") or "").strip() or _stable_uid(r)
        A("BEGIN:VEVENT")
        A(f"UID:{_ics_escape(uid)}")
        A(f"DTSTAMP:{_dt_utc_now()}")
        A(f"DTSTART;VALUE=DATE:{_date_to_ics(d1)}")
        A(f"DTEND;VALUE=DATE:{_date_to_ics(d2)}")
        A(f"SUMMARY:{_ics_escape(summary)}")
        A(f"DESCRIPTION:{_ics_escape(desc)}")
        A("END:VEVENT")
    A("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

# ============================== SMS (manuel) + Journal ==============================

def sms_message_arrivee(r: pd.Series) -> str:
    d1 = r.get("date_arrivee"); d2 = r.get("date_depart")
    d1s, d2s = fmt_day(d1), fmt_day(d2)
    nuitees = int(r.get("nuitees") or ((d2 - d1).days if isinstance(d1, date) and isinstance(d2, date) else 0))
    return (
        "VILLA TOBIAS\n"
        f"Plateforme : {r.get('plateforme','')}\n"
        f"Date d'arrivee : {d1s}  Date depart : {d2s}  Nombre de nuitees : {nuitees}\n\n"
        f"Bonjour {r.get('nom_client','')}\n"
        f"Telephone : {normalize_tel(r.get('telephone'))}\n\n"
        "Bienvenue chez nous !\n\n "
        "Nous sommes ravis de vous accueillir bientot. Pour organiser au mieux votre reception, pourriez-vous nous indiquer "
        "a quelle heure vous pensez arriver.\n\n "
        "Sachez egalement qu'une place de parking est a votre disposition dans l'immeuble, en cas de besoin.\n\n "
        "Nous vous souhaitons un excellent voyage et nous nous rejouissons de vous rencontrer.\n\n "
        "Annick & Charley"
    )

def sms_message_depart(r: pd.Series) -> str:
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

def append_sms_log(nature: str, apartment: str, client: str, tel: str, body: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    row = {"horodatage": now, "type": nature, "appartement": apartment, "nom_client": client, "telephone": tel, "message": body}
    if os.path.exists(SMS_LOG):
        df = pd.read_csv(SMS_LOG)
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    else:
        df = pd.DataFrame([row])
    df.to_csv(SMS_LOG, index=False)

# ============================== Vues principales ==============================

def vue_reservations(df_resa: pd.DataFrame, df_plats: pd.DataFrame):
    st.title("📋 Réservations (Multi)")
    df = ensure_schema_resa(df_resa)
    if df.empty:
        st.info("Aucune donnée.")
        return

    # Filtres
    colf = st.columns(4)
    apps = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    app = colf[0].selectbox("Appartement", apps)
    pfs  = ["Toutes"] + sorted(df["plateforme"].dropna().unique().tolist())
    pf  = colf[1].selectbox("Plateforme", pfs)
    years = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    annee = colf[2].selectbox("Année", ["Toutes"] + years, index=len(years)) if years else "Toutes"
    mois  = colf[3].selectbox("Mois", ["Tous"] + [f"{i:02d}" for i in range(1,13)])

    data = df.copy()
    if app != "Tous":
        data = data[data["appartement"] == app]
    if pf != "Toutes":
        data = data[data["plateforme"] == pf]
    if annee != "Toutes":
        data = data[data["AAAA"] == int(annee)]
    if mois != "Tous":
        data = data[data["MM"] == int(mois)]

    if data.empty:
        st.info("Aucune réservation avec ces filtres.")
        return

    chips_totaux(data)

    show = data.copy()
    for c in ["date_arrivee","date_depart"]:
        show[c] = show[c].apply(fmt_day)

    cols = ["appartement","nom_client","plateforme","telephone",
            "date_arrivee","date_depart","nuitees",
            "brut","commissions","frais_cb","net","menage","taxes_sejour","base",
            "%commission","sms_status"]
    cols = [c for c in cols if c in show.columns]
    st.dataframe(show[cols], use_container_width=True)

def vue_ajouter(df_resa: pd.DataFrame, df_plats: pd.DataFrame):
    st.title("➕ Ajouter une réservation")
    df = ensure_schema_resa(df_resa)

    # Entrées inline
    def inline(label, widget, key=None, **kw):
        c1, c2 = st.columns([1,2])
        with c1:
            st.markdown(f"**{label}**")
        with c2:
            return widget(label, key=key, label_visibility="collapsed", **kw)

    apt = inline("Appartement", st.text_input, key="add_appartement", value="")
    nom = inline("Nom client", st.text_input, key="add_nom", value="")
    tel = inline("Téléphone", st.text_input, key="add_tel", value="")
    pf_opts = sorted(ensure_schema_plateformes(df_plats)["plateforme"].unique().tolist() or ["Autre"])
    pf = inline("Plateforme", st.selectbox, key="add_pf", options=pf_opts, index=0)

    d1 = inline("Arrivée", st.date_input, key="add_d1", value=date.today())
    d2 = inline("Départ", st.date_input, key="add_d2", value=date.today() + timedelta(days=2), min_value=d1 + timedelta(days=1))

    brut = inline("Montant (brut)", st.number_input, key="add_brut", min_value=0.0, step=1.0, format="%.2f")
    cm   = inline("Commissions", st.number_input, key="add_cm", min_value=0.0, step=0.5, format="%.2f")
    cb   = inline("Frais CB", st.number_input, key="add_cb", min_value=0.0, step=0.5, format="%.2f")
    men  = inline("Ménage", st.number_input, key="add_men", min_value=0.0, step=0.5, format="%.2f")
    tax  = inline("Taxes séjour", st.number_input, key="add_tax", min_value=0.0, step=0.5, format="%.2f")

    net  = brut - cm - cb
    base = net - men - tax
    pct  = ((cm + cb) / brut * 100) if brut > 0 else 0.0

    c = st.columns(3)
    c[0].markdown(f"**Montant (net)**: {net:.2f} €")
    c[1].markdown(f"**Base**: {base:.2f} €")
    c[2].markdown(f"**% commission**: {pct:.2f} %")

    if st.button("Enregistrer"):
        if not isinstance(d1, date) or not isinstance(d2, date) or d2 <= d1:
            st.error("La date de départ doit être postérieure à la date d’arrivée (≥ +1 jour).")
            return
        ligne = {
            "appartement": apt.strip(),
            "nom_client": nom.strip(),
            "plateforme": pf,
            "telephone": normalize_tel(tel),
            "date_arrivee": d1,
            "date_depart": d2,
            "nuitees": (d2 - d1).days,
            "brut": float(brut),
            "commissions": float(cm),
            "frais_cb": float(cb),
            "net": round(net, 2),
            "menage": float(men),
            "taxes_sejour": float(tax),
            "base": round(base, 2),
            "%commission": round(pct, 2),
            "AAAA": d1.year,
            "MM": d1.month,
            "ical_uid": "",
            "sms_status": "🟠"
        }
        df_new = pd.concat([df, pd.DataFrame([ligne])], ignore_index=True)
        sauvegarder_fichier(df_new, df_plats)
        st.success("✅ Réservation ajoutée")
        st.rerun()

def vue_modifier(df_resa: pd.DataFrame, df_plats: pd.DataFrame):
    st.title("✏️ Modifier / Supprimer")
    df = ensure_schema_resa(df_resa)
    if df.empty:
        st.info("Aucune réservation.")
        return

    df["id"] = df.index
    df["aff"] = df["appartement"].astype(str) + " | " + df["nom_client"].astype(str) + " | " + df["plateforme"].astype(str) + " | " + df["date_arrivee"].apply(fmt_day)
    choix = st.selectbox("Choisir", df["aff"])
    i = df.loc[df["aff"] == choix, "id"].iloc[0]

    c1, c2 = st.columns(2)
    apt = c1.text_input("Appartement", df.at[i,"appartement"])
    nom = c2.text_input("Nom client", df.at[i,"nom_client"])
    tel = st.text_input("Téléphone", normalize_tel(df.at[i,"telephone"]))
    pf_opts = sorted(ensure_schema_plateformes(df_plats)["plateforme"].unique().tolist() or ["Autre"])
    try:
        idx_pf = pf_opts.index(df.at[i,"plateforme"]) if df.at[i,"plateforme"] in pf_opts else 0
    except:
        idx_pf = 0
    pf = st.selectbox("Plateforme", pf_opts, index=idx_pf)

    d1 = st.date_input("Arrivée", df.at[i,"date_arrivee"] if isinstance(df.at[i,"date_arrivee"], date) else date.today())
    d2 = st.date_input("Départ",  df.at[i,"date_depart"]  if isinstance(df.at[i,"date_depart"],  date) else date.today() + timedelta(days=1), min_value=d1 + timedelta(days=1))

    cols = st.columns(5)
    brut = cols[0].number_input("Brut", value=float(df.at[i,"brut"]) if pd.notna(df.at[i,"brut"]) else 0.0, step=1.0, format="%.2f")
    cm   = cols[1].number_input("Commissions", value=float(df.at[i,"commissions"]) if pd.notna(df.at[i,"commissions"]) else 0.0, step=0.5, format="%.2f")
    cb   = cols[2].number_input("Frais CB", value=float(df.at[i,"frais_cb"]) if pd.notna(df.at[i,"frais_cb"]) else 0.0, step=0.5, format="%.2f")
    men  = cols[3].number_input("Ménage", value=float(df.at[i,"menage"]) if pd.notna(df.at[i,"menage"]) else 0.0, step=0.5, format="%.2f")
    tax  = cols[4].number_input("Taxes séjour", value=float(df.at[i,"taxes_sejour"]) if pd.notna(df.at[i,"taxes_sejour"]) else 0.0, step=0.5, format="%.2f")

    net  = brut - cm - cb
    base = net - men - tax
    pct  = ((cm + cb) / brut * 100) if brut > 0 else 0.0
    st.markdown(f"**Net**: {net:.2f} € — **Base**: {base:.2f} € — **%**: {pct:.2f}")

    b1, b2 = st.columns(2)
    if b1.button("💾 Enregistrer"):
        df.at[i,"appartement"] = apt.strip()
        df.at[i,"nom_client"] = nom.strip()
        df.at[i,"telephone"] = normalize_tel(tel)
        df.at[i,"plateforme"] = pf
        df.at[i,"date_arrivee"] = d1
        df.at[i,"date_depart"] = d2
        df.at[i,"nuitees"] = (d2 - d1).days
        df.at[i,"brut"] = float(brut)
        df.at[i,"commissions"] = float(cm)
        df.at[i,"frais_cb"] = float(cb)
        df.at[i,"net"] = round(net, 2)
        df.at[i,"menage"] = float(men)
        df.at[i,"taxes_sejour"] = float(tax)
        df.at[i,"base"] = round(base, 2)
        df.at[i,"%commission"] = round(pct, 2)
        df.at[i,"AAAA"] = d1.year
        df.at[i,"MM"] = d1.month
        sauvegarder_fichier(df.drop(columns=["id","aff"]), df_plats)
        st.success("✅ Modifié")
        st.rerun()

    if b2.button("🗑 Supprimer"):
        df2 = df.drop(index=i).drop(columns=["id","aff"])
        sauvegarder_fichier(df2, df_plats)
        st.warning("Supprimé.")
        st.rerun()

def vue_platforms(df_plats: pd.DataFrame, df_resa: pd.DataFrame):
    st.title("🎨 Plateformes & couleurs")
    plats = ensure_schema_plateformes(df_plats).copy()
    st.dataframe(plats, use_container_width=True)
    with st.expander("➕ Ajouter / mettre à jour"):
        p = st.text_input("Nom plateforme")
        color = st.color_picker("Couleur", value="#999999")
        if st.button("Enregistrer plateforme"):
            if not p.strip():
                st.error("Nom requis")
            else:
                if p in plats["plateforme"].values:
                    plats.loc[plats["plateforme"] == p, "couleur_hex"] = color
                else:
                    plats = pd.concat([plats, pd.DataFrame([{"plateforme": p, "couleur_hex": color}])], ignore_index=True)
                sauvegarder_fichier(df_resa, plats)
                st.success("✅ Plateforme sauvegardée")
                st.rerun()

def _build_colored_calendar_html(weeks, colors_by_day, headers=("L","M","M","J","V","S","D")):
    # Génère une table HTML responsive avec couleurs par jour
    css = """
    <style>
      .cal { border-collapse: collapse; width: 100%; table-layout: fixed; }
      .cal th, .cal td { border: 1px solid rgba(127,127,127,0.25); text-align: center; vertical-align: top; }
      .cal th { padding: 6px 0; font-weight: 700; }
      .cal td { height: 48px; padding: 0; }
      .cal .cell { display:flex; align-items:flex-start; justify-content:flex-start; height:100%; padding:6px; font-weight:600; }
      @media (max-width: 480px) {
        .cal td { height: 40px; }
        .cal .cell { padding: 4px; font-size: 0.95rem; }
      }
    </style>
    """
    html = [css, '<table class="cal">', "<thead><tr>"]
    for h in headers:
        html.append(f"<th>{h}</th>")
    html.append("</tr></thead><tbody>")
    for wk in weeks:
        html.append("<tr>")
        for d in wk:
            if d == 0:
                html.append("<td><div class='cell' style='background:#0000'></div></td>")
            else:
                color = colors_by_day.get(d, "#0000")
                html.append(f"<td><div class='cell' style='background:{color}'>{d}</div></td>")
        html.append("</tr>")
    html.append("</tbody></table>")
    return "".join(html)

def vue_calendrier(df_resa: pd.DataFrame, df_plats: pd.DataFrame):
    st.title("📅 Calendrier (mobile lisible)")

    df = ensure_schema_resa(df_resa)
    plats = ensure_schema_plateformes(df_plats)
    if df.empty:
        st.info("Aucune donnée.")
        return

    # Filtres en ligne
    cols = st.columns(3)
    apps = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    app = cols[0].selectbox("Appartement", apps)
    mois_nom = cols[1].selectbox("Mois", list(calendar.month_name)[1:], index=max(0, date.today().month-1))
    years = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    if not years:
        st.warning("Aucune année")
        return
    annee = cols[2].selectbox("Année", years, index=len(years)-1)

    # Mapping plateforme -> couleur
    color_map = {row["plateforme"]: row["couleur_hex"] for _, row in plats.iterrows()}
    default_color = "#bbbbbb"

    # Données filtrées par appartement
    data = df.copy()
    if app != "Tous":
        data = data[data["appartement"] == app]

    mois = list(calendar.month_name).index(mois_nom)
    calendar.setfirstweekday(calendar.MONDAY)
    weeks = calendar.monthcalendar(annee, mois)

    # Couleur par jour
    colors_by_day = {}
    day_has_booking = {}
    for wk in weeks:
        for d in wk:
            if d == 0:
                continue
            current = date(annee, mois, d)
            day_rows = data[(data["date_arrivee"] <= current) & (data["date_depart"] > current)]
            if day_rows.empty:
                colors_by_day[d] = "#0000"
                continue
            plats_day = day_rows["plateforme"].dropna().unique().tolist()
            if len(plats_day) == 1:
                colors_by_day[d] = color_map.get(plats_day[0], default_color)
            else:
                colors_by_day[d] = default_color
            day_has_booking[d] = True

    # Rendu HTML colorisé
    st.markdown(_build_colored_calendar_html(weeks, colors_by_day), unsafe_allow_html=True)

    # Légende
    with st.expander("Légende plateformes"):
        if plats.empty:
            st.caption("Aucune plateforme définie.")
        else:
            html = "<div style='display:flex;flex-wrap:wrap;gap:8px'>"
            for _, r in plats.iterrows():
                html += f"<div style='display:flex;align-items:center;gap:6px;border:1px solid rgba(127,127,127,.25);padding:4px 8px;border-radius:8px'>"
                html += f"<span style='display:inline-block;width:14px;height:14px;background:{r['couleur_hex']};border-radius:3px'></span>"
                html += f"<span>{r['plateforme']}</span></div>"
            html += "</div>"
            st.markdown(html, unsafe_allow_html=True)

    # Détail du jour
    jours_dispos = sorted(day_has_booking.keys())
    if jours_dispos:
        jour_pick = st.selectbox("Voir le détail du jour", jours_dispos, format_func=lambda x: f"{x:02d}")
        day_date = date(annee, mois, int(jour_pick))
        subset = data[(data["date_arrivee"] <= day_date) & (data["date_depart"] > day_date)].copy()
        if not subset.empty:
            subset["date_arrivee"] = subset["date_arrivee"].apply(fmt_day)
            subset["date_depart"] = subset["date_depart"].apply(fmt_day)
            st.dataframe(
                subset[["appartement","plateforme","nom_client","telephone","date_arrivee","date_depart","nuitees","brut","net","base"]],
                use_container_width=True
            )
        else:
            st.info("Aucune réservation ce jour.")
    else:
        st.info("Aucune réservation ce mois.")

def vue_rapport(df_resa: pd.DataFrame):
    st.title("📊 Rapport")
    df = ensure_schema_resa(df_resa)
    if df.empty:
        st.info("Aucune donnée.")
        return

    c1, c2, c3, c4 = st.columns(4)
    apps = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    app = c1.selectbox("Appartement", apps)
    pfs  = ["Toutes"] + sorted(df["plateforme"].dropna().unique().tolist())
    pf   = c2.selectbox("Plateforme", pfs)
    years = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    annee = c3.selectbox("Année", years, index=len(years)-1) if years else None
    mois  = c4.selectbox("Mois", ["Tous"] + [f"{i:02d}" for i in range(1,13)])

    data = df.copy()
    if app != "Tous":
        data = data[data["appartement"] == app]
    if pf != "Toutes":
        data = data[data["plateforme"] == pf]
    if annee:
        data = data[data["AAAA"] == int(annee)]
    if mois != "Tous":
        data = data[data["MM"] == int(mois)]

    if data.empty:
        st.info("Aucune donnée avec ces filtres.")
        return

    # Détail trié (noms visibles)
    detail = data.copy()
    for c in ["date_arrivee","date_depart"]:
        detail[c] = detail[c].apply(fmt_day)
    by = [c for c in ["date_arrivee","appartement","nom_client"] if c in detail.columns]
    if by:
        detail = detail.sort_values(by=by).reset_index(drop=True)

    cols = ["appartement","nom_client","plateforme","telephone","date_arrivee","date_depart","nuitees",
            "brut","commissions","frais_cb","net","menage","taxes_sejour","base","%commission","sms_status"]
    cols = [c for c in cols if c in detail.columns]
    st.dataframe(detail[cols], use_container_width=True)

    # Totaux
    chips_totaux(data)

    # Agrégats mensuels par plateforme
    stats = (data.groupby(["MM","plateforme"])
                .agg(brut=("brut","sum"),
                     net=("net","sum"),
                     base=("base","sum"),
                     nuitees=("nuitees","sum"))
                .reset_index())
    stats = stats.sort_values(["MM","plateforme"])

    def chart(metric, title, unit):
        pivot = stats.pivot(index="MM", columns="plateforme", values=metric).fillna(0).sort_index()
        if pivot.empty:
            return
        pivot.index = [f"{int(m):02d}" for m in pivot.index]
        st.markdown(f"**{title} ({'€' if unit=='€' else unit})**")
        st.bar_chart(pivot)

    chart("brut", "Montant (brut)", "€")
    chart("net", "Montant (net)", "€")
    chart("nuitees", "Nuitées", "N")

    # Export XLSX du détail filtré
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        detail[cols].to_excel(w, index=False)
    st.download_button(
        "⬇️ Télécharger le détail (XLSX)",
        data=buf.getvalue(),
        file_name=f"rapport_multi_{app if app!='Tous' else 'all'}_{pf if pf!='Toutes' else 'all'}_{annee or 'all'}_{mois}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

def vue_sms(df_resa: pd.DataFrame):
    st.title("✉️ SMS (manuel) + Journal")
    df = ensure_schema_resa(df_resa)
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
            tel = normalize_tel(r.get("telephone"))
            body = sms_message_arrivee(r)
            c1, c2, c3, c4 = st.columns([2,2,1,1])
            c1.markdown(f"**{r.get('appartement','')}** — {r.get('nom_client','')} ({r.get('plateforme','')})")
            c2.markdown(f"{fmt_day(r.get('date_arrivee'))} → {fmt_day(r.get('date_depart'))} • {int(r.get('nuitees') or 0)} nuitées")
            if tel:
                c3.link_button("📞 Appeler", f"tel:{tel}")
                c4.link_button("📩 SMS", f"sms:{tel}?&body={quote(body)}")
            st.code(body)
            if st.button(f"Marquer SMS envoyé ({r.get('nom_client','')})", key=f"sms_ok_{idx}"):
                # Met le statut à 🟢 et journalise
                xls = charger_fichier()
                real = ensure_schema_resa(xls.get("Réservations", pd.DataFrame()))
                real.loc[(real["appartement"]==r["appartement"]) &
                         (real["nom_client"]==r["nom_client"]) &
                         (real["date_arrivee"]==r["date_arrivee"]), "sms_status"] = "🟢"
                sauvegarder_fichier(real, ensure_schema_plateformes(xls.get("Plateformes", pd.DataFrame())))
                append_sms_log("arrivee", r.get("appartement",""), r.get("nom_client",""), tel, body)
                st.success("Noté comme envoyé")
                st.rerun()
            st.divider()

    # Relance +24h après départ
    st.subheader("🕒 Relance (+24h après départ)")
    dep = df[df["date_depart"] == hier].copy()
    if dep.empty:
        st.info("Aucun départ hier.")
    else:
        for idx, r in dep.reset_index(drop=True).iterrows():
            tel = normalize_tel(r.get("telephone"))
            body = sms_message_depart(r)
            c1, c2, c3, c4 = st.columns([2,2,1,1])
            c1.markdown(f"**{r.get('appartement','')}** — {r.get('nom_client','')} ({r.get('plateforme','')})")
            c2.markdown(f"Départ: {fmt_day(r.get('date_depart'))}")
            if tel:
                c3.link_button("📞 Appeler", f"tel:{tel}")
                c4.link_button("📩 SMS", f"sms:{tel}?&body={quote(body)}")
            st.code(body)
            if st.button(f"Marquer relance envoyée ({r.get('nom_client','')})", key=f"sms_dep_{idx}"):
                append_sms_log("depart+24h", r.get("appartement",""), r.get("nom_client",""), tel, body)
                st.success("Relance notée comme envoyée")
                st.rerun()
            st.divider()

    # Journal des SMS
    st.subheader("📜 Journal des SMS")
    if os.path.exists(SMS_LOG):
        log = pd.read_csv(SMS_LOG)
        st.dataframe(log, use_container_width=True)
    else:
        st.info("Aucun SMS enregistré pour le moment.")

def vue_export_ics(df_resa: pd.DataFrame):
    st.title("📤 Export ICS")
    df = ensure_schema_resa(df_resa)
    if df.empty:
        st.info("Aucune donnée à exporter.")
        return

    c1, c2, c3, c4 = st.columns(4)
    apps = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    app = c1.selectbox("Appartement", apps)
    pfs  = ["Toutes"] + sorted(df["plateforme"].dropna().unique().tolist())
    pf   = c2.selectbox("Plateforme", pfs)
    years = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    annee = c3.selectbox("Année", ["Toutes"] + years, index=len(years)) if years else "Toutes"
    mois  = c4.selectbox("Mois", ["Tous"] + [f"{i:02d}" for i in range(1,13)])

    data = df.copy()
    if app != "Tous":
        data = data[data["appartement"] == app]
    if pf != "Toutes":
        data = data[data["plateforme"] == pf]
    if annee != "Toutes":
        data = data[data["AAAA"] == int(annee)]
    if mois != "Tous":
        data = data[data["MM"] == int(mois)]

    if data.empty:
        st.info("Aucune réservation pour ces filtres.")
        return

    ics_txt = df_to_ics(data, cal_name="Multi – Réservations")
    st.download_button(
        "⬇️ Télécharger reservations.ics",
        data=ics_txt.encode("utf-8"),
        file_name="reservations.ics",
        mime="text/calendar"
    )
    st.caption("Google Agenda → Paramètres → Importer & exporter → Importer → sélectionnez le .ics.")

# ============================== 👥 Liste clients ==============================

def vue_clients(df_resa: pd.DataFrame):
    st.title("👥 Liste des clients")
    df = ensure_schema_resa(df_resa)
    if df.empty:
        st.info("Aucune donnée.")
        return

    c1, c2, c3, c4 = st.columns(4)
    apps = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    app = c1.selectbox("Appartement", apps)
    pfs  = ["Toutes"] + sorted(df["plateforme"].dropna().unique().tolist())
    pf   = c2.selectbox("Plateforme", pfs)
    years = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    annee = c3.selectbox("Année", ["Toutes"] + years, index=len(years)) if years else "Toutes"
    mois  = c4.selectbox("Mois", ["Tous"] + [f"{i:02d}" for i in range(1,13)])

    data = df.copy()
    if app != "Tous":
        data = data[data["appartement"] == app]
    if pf != "Toutes":
        data = data[data["plateforme"] == pf]
    if annee != "Toutes":
        data = data[data["AAAA"] == int(annee)]
    if mois != "Tous":
        data = data[data["MM"] == int(mois)]

    if data.empty:
        st.info("Aucun client pour ces filtres.")
        return

    # €/nuit
    data["brut/nuit"] = data.apply(lambda r: round((r["brut"]/r["nuitees"]) if r["nuitees"] else 0,2), axis=1)
    data["net/nuit"]  = data.apply(lambda r: round((r["net"]/r["nuitees"])  if r["nuitees"] else 0,2), axis=1)
    data["base/nuit"] = data.apply(lambda r: round((r["base"]/r["nuitees"]) if r["nuitees"] else 0,2), axis=1)

    show = data.copy()
    for c in ["date_arrivee","date_depart"]:
        show[c] = show[c].apply(fmt_day)

    cols = [
        "appartement","nom_client","plateforme","telephone",
        "date_arrivee","date_depart","nuitees",
        "brut","net","base","%commission",
        "brut/nuit","net/nuit","base/nuit","sms_status"
    ]
    cols = [c for c in cols if c in show.columns]
    st.dataframe(show[cols], use_container_width=True)

    st.download_button(
        "📥 Télécharger (CSV)",
        data=show[cols].to_csv(index=False).encode("utf-8"),
        file_name="clients_multi.csv",
        mime="text/csv"
    )

# ============================== Maintenance (cache) ==============================

def render_cache_tools():
    st.sidebar.markdown("---")
    st.sidebar.markdown("## 🧰 Maintenance")
    if st.sidebar.button("♻️ Vider le cache et relancer"):
        try: st.cache_data.clear()
        except: pass
        try: st.cache_resource.clear()
        except: pass
        st.sidebar.success("Cache vidé. Redémarrage…")
        st.rerun()

# ============================== APP ==============================

def main():
    st.set_page_config(page_title="🏢 Réservations Multi", layout="wide")

    # Fichier
    st.sidebar.title("📁 Fichier")
    xls = charger_fichier()
    df_resa = ensure_schema_resa(xls.get("Réservations", pd.DataFrame()))
    df_plat = ensure_schema_plateformes(xls.get("Plateformes", pd.DataFrame()))
    telecharger_fichier(df_resa, df_plat)
    restaurer_fichier()

    # Navigation
    st.sidebar.title("🧭 Navigation")
    onglet = st.sidebar.radio(
        "Aller à",
        ["📋 Réservations","➕ Ajouter","✏️ Modifier/Supprimer",
         "🎨 Plateformes","📅 Calendrier","📊 Rapport","👥 Liste clients","✉️ SMS","📤 Export ICS"]
    )

    render_cache_tools()

    if onglet == "📋 Réservations":
        vue_reservations(df_resa, df_plat)
    elif onglet == "➕ Ajouter":
        vue_ajouter(df_resa, df_plat)
    elif onglet == "✏️ Modifier/Supprimer":
        vue_modifier(df_resa, df_plat)
    elif onglet == "🎨 Plateformes":
        vue_platforms(df_plat, df_resa)
    elif onglet == "📅 Calendrier":
        vue_calendrier(df_resa, df_plat)
    elif onglet == "📊 Rapport":
        vue_rapport(df_resa)
    elif onglet == "👥 Liste clients":
        vue_clients(df_resa)
    elif onglet == "✉️ SMS":
        vue_sms(df_resa)
    elif onglet == "📤 Export ICS":
        vue_export_ics(df_resa)

if __name__ == "__main__":
    main()