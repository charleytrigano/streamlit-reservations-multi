# app_multi.py — Réservations Multi (COMPLET)

import streamlit as st
import pandas as pd
import numpy as np
import calendar
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import json, os, hashlib
from urllib.parse import quote

FICHIER_XLSX = "reservations_multi.xlsx"
SMS_LOG = "sms_log_multi.csv"
PLAT_FILE = "plateformes_multi.json"

# ============================ UTILS GÉNÉRAUX ============================

def normalize_tel(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = str(x).strip().replace(" ", "")
    if s.endswith(".0"):
        s = s[:-2]
    return s

def to_date_only(x):
    if pd.isna(x) or x is None:
        return None
    try:
        return pd.to_datetime(x).date()
    except Exception:
        return None

def fmt_date(d):
    return d.strftime("%Y/%m/%d") if isinstance(d, date) else ""

def _is_total_row(row: pd.Series) -> bool:
    # Pour l’instant, pas de ligne "Total" en multi, mais on garde la logique si besoin
    name_is_total = str(row.get("nom_client","")).strip().lower() == "total"
    return name_is_total

def _split_totals(df: pd.DataFrame):
    if df is None or df.empty:
        return df, df
    mask = df.apply(_is_total_row, axis=1)
    return df[~mask].copy(), df[mask].copy()

def _sort_core(df: pd.DataFrame):
    if df is None or df.empty: return df
    by = [c for c in ["appartement","date_arrivee","nom_client"] if c in df.columns]
    return df.sort_values(by=by, na_position="last").reset_index(drop=True)

# ============================ SCHÉMA & CALCULS ============================

BASE_COLS = [
    "appartement","nom_client","plateforme","telephone",
    "date_arrivee","date_depart","nuitees",
    "montant_net","commissions","frais_cb","montant_brut",
    "menage","taxes_sejour","base","%",
    "AAAA","MM","sms"
]

def ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        df = pd.DataFrame(columns=BASE_COLS)

    df = df.copy()

    # Dates
    for c in ["date_arrivee","date_depart"]:
        if c in df.columns:
            df[c] = df[c].apply(to_date_only)

    # Téléphone
    if "telephone" in df.columns:
        df["telephone"] = df["telephone"].apply(normalize_tel)
    else:
        df["telephone"] = ""

    # Colonnes présentes / défauts
    defaults = {
        "appartement":"Appartement A",
        "nom_client":"", "plateforme":"Autre",
        "montant_net":0.0, "commissions":0.0, "frais_cb":0.0,
        "menage":0.0, "taxes_sejour":0.0,
        "montant_brut":0.0, "base":0.0, "%":0.0,
        "nuitees":np.nan, "AAAA":np.nan, "MM":np.nan,
        "sms":"🟠 En attente"
    }
    for k,v in defaults.items():
        if k not in df.columns:
            df[k] = v

    # Numériques
    num_cols = ["montant_net","commissions","frais_cb","montant_brut",
                "menage","taxes_sejour","base","%"]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Calculs financiers
    df["montant_brut"] = (df["montant_net"].fillna(0) +
                          df["commissions"].fillna(0) +
                          df["frais_cb"].fillna(0)).round(2)
    df["base"] = (df["montant_brut"].fillna(0) -
                  df["menage"].fillna(0) -
                  df["taxes_sejour"].fillna(0)).round(2)
    with pd.option_context("mode.use_inf_as_na", True):
        df["%"] = ((df["montant_brut"] - df["montant_net"]) / df["montant_brut"] * 100).fillna(0).round(2)

    # Nuitées
    def _nuits(r):
        d1, d2 = r.get("date_arrivee"), r.get("date_depart")
        return (d2 - d1).days if isinstance(d1, date) and isinstance(d2, date) else np.nan
    df["nuitees"] = df.apply(_nuits, axis=1)

    # AAAA / MM
    df["AAAA"] = df["date_arrivee"].apply(lambda d: d.year if isinstance(d, date) else np.nan).astype("Int64")
    df["MM"]   = df["date_arrivee"].apply(lambda d: d.month if isinstance(d, date) else np.nan).astype("Int64")

    # Ordre
    cols = BASE_COLS + [c for c in df.columns if c not in BASE_COLS]
    df = df[cols]

    return df

# ============================ EXCEL I/O ============================

@st.cache_data(show_spinner=False)
def _read_excel_cached(path: str, mtime: float):
    return pd.read_excel(path, converters={"telephone": normalize_tel})

def charger_donnees() -> pd.DataFrame:
    if not os.path.exists(FICHIER_XLSX):
        return ensure_schema(pd.DataFrame())
    try:
        mtime = os.path.getmtime(FICHIER_XLSX)
        df = _read_excel_cached(FICHIER_XLSX, mtime)
        return ensure_schema(df)
    except Exception as e:
        st.error(f"Erreur de lecture Excel : {e}")
        return ensure_schema(pd.DataFrame())

def sauvegarder_donnees(df: pd.DataFrame):
    df = ensure_schema(df)
    core, totals = _split_totals(df)
    out = pd.concat([_sort_core(core), totals], ignore_index=True)
    try:
        with pd.ExcelWriter(FICHIER_XLSX, engine="openpyxl") as w:
            out.to_excel(w, index=False)
        st.cache_data.clear()
        st.success("💾 Sauvegarde Excel effectuée.")
    except Exception as e:
        st.error(f"Échec de sauvegarde Excel : {e}")

def bouton_fichier_sidebar(df):
    st.sidebar.markdown("### 💾 Fichier")
    # Télécharger
    buf = BytesIO()
    try:
        ensure_schema(df).to_excel(buf, index=False, engine="openpyxl")
        data_xlsx = buf.getvalue()
    except Exception as e:
        st.sidebar.error(f"Export XLSX indisponible : {e}")
        data_xlsx = None
    st.sidebar.download_button(
        "⬇️ Sauvegarde XLSX",
        data=data_xlsx if data_xlsx else b"",
        file_name=FICHIER_XLSX,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=(data_xlsx is None)
    )
    # Restaurer
    up = st.sidebar.file_uploader("📤 Restauration XLSX", type=["xlsx"])
    if up is not None:
        try:
            df_new = pd.read_excel(up, converters={"telephone": normalize_tel})
            df_new = ensure_schema(df_new)
            sauvegarder_donnees(df_new)
            st.sidebar.success("✅ Fichier restauré.")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Erreur import: {e}")

# ============================ PLATEFORMES (COULEURS) ============================

DEFAULT_PLAT = {
    "Booking": "#3b82f6",   # bleu
    "Airbnb": "#10b981",    # vert
    "Autre": "#f59e0b"      # orange
}

def load_platforms() -> dict:
    if os.path.exists(PLAT_FILE):
        try:
            with open(PLAT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return data
        except Exception:
            pass
    return DEFAULT_PLAT.copy()

def save_platforms(d: dict):
    try:
        with open(PLAT_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"Erreur sauvegarde plateformes: {e}")

def plateforme_color_badge(name: str, colors: dict):
    color = colors.get(name, "#888888")
    return f"<span style='background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px'>{name}</span>"

def ui_platform_manager(colors: dict):
    st.sidebar.markdown("### 🎨 Plateformes & couleurs")
    st.sidebar.caption("Ajoute/modifie des plateformes et leurs couleurs (persisté en JSON).")
    with st.sidebar.expander("Gérer les plateformes", expanded=False):
        for p in sorted(colors.keys()):
            cols = st.columns([2,1,1])
            with cols[0]:
                st.markdown(plateforme_color_badge(p, colors), unsafe_allow_html=True)
            with cols[1]:
                new_color = st.color_picker("", value=colors[p], key=f"col_{p}")
            with cols[2]:
                if st.button("🗑", key=f"del_{p}"):
                    if p in DEFAULT_PLAT and len(colors) <= 1:
                        st.warning("Conserve au moins une plateforme.")
                    else:
                        colors.pop(p, None)
                        save_platforms(colors)
                        st.rerun()
            if new_color != colors[p]:
                colors[p] = new_color
                save_platforms(colors)
        st.divider()
        new_p = st.text_input("Nouvelle plateforme", key="new_pf")
        new_c = st.color_picker("Couleur", value="#8b5cf6", key="new_pf_col")
        if st.button("➕ Ajouter"):
            p = new_p.strip()
            if p:
                colors[p] = new_c
                save_platforms(colors)
                st.success(f"Ajouté : {p}")
                st.rerun()

# ============================ VUES ============================

def totaux_html(df):
    if df.empty:
        return ""
    t_brut = df["montant_brut"].sum(skipna=True)
    t_net  = df["montant_net"].sum(skipna=True)
    t_chg  = (df["commissions"].sum(skipna=True) + df["frais_cb"].sum(skipna=True))
    t_nuit = df["nuitees"].sum(skipna=True)
    pct    = ((t_brut - t_net) / t_brut * 100) if t_brut else 0
    return f"""
<style>
.chips {{ display:flex; flex-wrap:wrap; gap:12px; margin:8px 0 16px; }}
.chip {{ padding:10px 12px; border-radius:10px; background: rgba(127,127,127,0.10); border:1px solid rgba(127,127,127,0.2) }}
.chip b {{ display:block; margin-bottom:4px; }}
</style>
<div class='chips'>
  <div class='chip'><b>Total Brut</b><div>{t_brut:,.2f} €</div></div>
  <div class='chip'><b>Total Net</b><div>{t_net:,.2f} €</div></div>
  <div class='chip'><b>Total Commissions+CB</b><div>{t_chg:,.2f} €</div></div>
  <div class='chip'><b>Total Nuitées</b><div>{int(t_nuit) if pd.notna(t_nuit) else 0}</div></div>
  <div class='chip'><b>Commission moy.</b><div>{pct:.2f} %</div></div>
</div>
"""

def vue_reservations(df, colors):
    st.title("🏘️ Réservations (multi)")
    if df.empty:
        st.info("Aucune donnée.")
        return
    st.markdown(totaux_html(df), unsafe_allow_html=True)

    show = df.copy()
    for c in ["date_arrivee","date_depart"]:
        show[c] = show[c].apply(fmt_date)
    # badges couleurs
    show["plateforme"] = show["plateforme"].apply(lambda p: st._repr_html_(plateforme_color_badge(str(p), colors)) or p)  # fallback
    # affichage
    st.dataframe(df.assign(
        date_arrivee=df["date_arrivee"].apply(fmt_date),
        date_depart=df["date_depart"].apply(fmt_date)
    ), use_container_width=True)

def vue_ajouter(df, colors):
    st.title("➕ Ajouter")
    c1,c2,c3 = st.columns(3)
    appartement = c1.text_input("Appartement", value="Appartement A")
    plateforme  = c2.selectbox("Plateforme", options=sorted(colors.keys()))
    nom         = c3.text_input("Nom client", value="")

    c4,c5 = st.columns(2)
    tel    = c4.text_input("Téléphone (+33...)", value="")
    arrivee= c5.date_input("Arrivée", value=date.today())
    depart = st.date_input("Départ", value=arrivee+timedelta(days=1), min_value=arrivee+timedelta(days=1))

    c6,c7,c8 = st.columns(3)
    net   = c6.number_input("Montant net (€)", min_value=0.0, step=1.0, format="%.2f")
    com   = c7.number_input("Commissions (€)", min_value=0.0, step=1.0, format="%.2f")
    fcb   = c8.number_input("Frais CB (€)",    min_value=0.0, step=1.0, format="%.2f")

    montant_brut = round(net + com + fcb, 2)

    c9,c10 = st.columns(2)
    menage = c9.number_input("Ménage (€)", min_value=0.0, step=1.0, format="%.2f")
    taxe   = c10.number_input("Taxes séjour (€)", min_value=0.0, step=1.0, format="%.2f")

    base = round(montant_brut - menage - taxe, 2)
    pct  = round(((montant_brut - net)/montant_brut*100) if montant_brut else 0.0, 2)

    c11,c12,c13 = st.columns(3)
    c11.number_input("Montant brut (calc.)", value=montant_brut, disabled=True, step=0.01, format="%.2f")
    c12.number_input("Base (calc.)", value=base, disabled=True, step=0.01, format="%.2f")
    c13.number_input("Commission % (calc.)", value=pct, disabled=True, step=0.01, format="%.2f")

    if st.button("Enregistrer"):
        if depart < arrivee + timedelta(days=1):
            st.error("Le départ doit être au moins le lendemain.")
            return
        ligne = {
            "appartement": appartement.strip() or "Appartement A",
            "nom_client": nom.strip(),
            "plateforme": plateforme,
            "telephone": normalize_tel(tel),
            "date_arrivee": arrivee,
            "date_depart": depart,
            "nuitees": (depart - arrivee).days,
            "montant_net": float(net),
            "commissions": float(com),
            "frais_cb": float(fcb),
            "montant_brut": montant_brut,
            "menage": float(menage),
            "taxes_sejour": float(taxe),
            "base": base,
            "%": pct,
            "AAAA": arrivee.year,
            "MM": arrivee.month,
            "sms": "🟠 En attente"
        }
        df2 = pd.concat([df, pd.DataFrame([ligne])], ignore_index=True)
        sauvegarder_donnees(df2)
        st.success("✅ Réservation ajoutée")
        st.rerun()

def vue_modifier(df, colors):
    st.title("✏️ Modifier / Supprimer")
    if df.empty:
        st.info("Aucune réservation.")
        return
    df["identifiant"] = df["appartement"].astype(str)+" | "+df["nom_client"].astype(str)+" | "+df["date_arrivee"].apply(fmt_date)
    choix = st.selectbox("Choisir", df["identifiant"])
    ixs = df.index[df["identifiant"] == choix]
    if len(ixs)==0:
        st.warning("Sélection invalide.")
        return
    i = ixs[0]

    c1,c2,c3 = st.columns(3)
    appartement = c1.text_input("Appartement", value=df.at[i,"appartement"])
    plateforme  = c2.selectbox("Plateforme", options=sorted(colors.keys()),
                               index=max(0, list(sorted(colors.keys())).index(df.at[i,"plateforme"]) if df.at[i,"plateforme"] in colors else 0))
    nom         = c3.text_input("Nom client", value=df.at[i,"nom_client"])

    c4,c5 = st.columns(2)
    tel     = c4.text_input("Téléphone", value=df.at[i,"telephone"])
    arrivee = c5.date_input("Arrivée", value=df.at[i,"date_arrivee"] if isinstance(df.at[i,"date_arrivee"], date) else date.today())
    depart  = st.date_input("Départ", value=df.at[i,"date_depart"] if isinstance(df.at[i,"date_depart"], date) else arrivee+timedelta(days=1),
                            min_value=arrivee+timedelta(days=1))

    c6,c7,c8 = st.columns(3)
    net = c6.number_input("Montant net (€)", min_value=0.0, value=float(df.at[i,"montant_net"]) if pd.notna(df.at[i,"montant_net"]) else 0.0, step=1.0, format="%.2f")
    com = c7.number_input("Commissions (€)", min_value=0.0, value=float(df.at[i,"commissions"]) if pd.notna(df.at[i,"commissions"]) else 0.0, step=1.0, format="%.2f")
    fcb = c8.number_input("Frais CB (€)",    min_value=0.0, value=float(df.at[i,"frais_cb"]) if pd.notna(df.at[i,"frais_cb"]) else 0.0, step=1.0, format="%.2f")

    montant_brut = round(net + com + fcb, 2)

    c9,c10 = st.columns(2)
    menage = c9.number_input("Ménage (€)", min_value=0.0, value=float(df.at[i,"menage"]) if pd.notna(df.at[i,"menage"]) else 0.0, step=1.0, format="%.2f")
    taxe   = c10.number_input("Taxes séjour (€)", min_value=0.0, value=float(df.at[i,"taxes_sejour"]) if pd.notna(df.at[i,"taxes_sejour"]) else 0.0, step=1.0, format="%.2f")

    base = round(montant_brut - menage - taxe, 2)
    pct  = round(((montant_brut - net)/montant_brut*100) if montant_brut else 0.0, 2)

    c11,c12,c13 = st.columns(3)
    c11.number_input("Montant brut (calc.)", value=montant_brut, disabled=True, step=0.01, format="%.2f")
    c12.number_input("Base (calc.)", value=base, disabled=True, step=0.01, format="%.2f")
    c13.number_input("Commission % (calc.)", value=pct, disabled=True, step=0.01, format="%.2f")

    cA,cB = st.columns(2)
    if cA.button("💾 Enregistrer"):
        if depart < arrivee + timedelta(days=1):
            st.error("Le départ doit être au moins le lendemain.")
            return
        df.at[i,"appartement"] = appartement.strip() or "Appartement A"
        df.at[i,"plateforme"]  = plateforme
        df.at[i,"nom_client"]  = nom.strip()
        df.at[i,"telephone"]   = normalize_tel(tel)
        df.at[i,"date_arrivee"]= arrivee
        df.at[i,"date_depart"] = depart
        df.at[i,"nuitees"]     = (depart - arrivee).days
        df.at[i,"montant_net"] = float(net)
        df.at[i,"commissions"] = float(com)
        df.at[i,"frais_cb"]    = float(fcb)
        df.at[i,"montant_brut"]= montant_brut
        df.at[i,"menage"]      = float(menage)
        df.at[i,"taxes_sejour"]= float(taxe)
        df.at[i,"base"]        = base
        df.at[i,"%"]           = pct
        df.at[i,"AAAA"]        = arrivee.year
        df.at[i,"MM"]          = arrivee.month
        df.drop(columns=["identifiant"], inplace=True, errors="ignore")
        sauvegarder_donnees(df)
        st.success("✅ Modifié")
        st.rerun()

    if cB.button("🗑 Supprimer"):
        df2 = df.drop(index=i)
        df2.drop(columns=["identifiant"], inplace=True, errors="ignore")
        sauvegarder_donnees(df2)
        st.warning("Supprimé")
        st.rerun()

def vue_calendrier(df, colors):
    st.title("📅 Calendrier")
    if df.empty:
        st.info("Aucune donnée.")
        return
    c1,c2 = st.columns(2)
    mois_nom = c1.selectbox("Mois", list(calendar.month_name)[1:], index=max(0, date.today().month-1))
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    if not annees:
        st.info("Pas d'année disponible.")
        return
    annee = c2.selectbox("Année", annees, index=len(annees)-1)
    mois_index = list(calendar.month_name).index(mois_nom)
    nb_jours = calendar.monthrange(annee, mois_index)[1]
    jours = [date(annee, mois_index, j+1) for j in range(nb_jours)]
    planning = {j: [] for j in jours}

    for _, r in df.iterrows():
        d1, d2 = r["date_arrivee"], r["date_depart"]
        if not (isinstance(d1, date) and isinstance(d2, date)): continue
        for j in jours:
            if d1 <= j < d2:
                tag = r["plateforme"]
                badge = f"<span style='background:{colors.get(tag, '#888')};color:#fff;padding:1px 6px;border-radius:8px;'>{tag}</span>"
                planning[j].append(f"{badge} {r['nom_client']}")

    table = []
    for semaine in calendar.monthcalendar(annee, mois_index):
        ligne = []
        for jour in semaine:
            if jour == 0:
                ligne.append("")
            else:
                d = date(annee, mois_index, jour)
                html = f"<b>{jour}</b><br>" + "<br>".join(planning.get(d, []))
                ligne.append(html)
        table.append(ligne)

    # Affichage HTML pour conserver couleurs
    df_html = pd.DataFrame(table, columns=["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"])
    st.write(df_html.to_html(escape=False, index=False), unsafe_allow_html=True)

def vue_rapport(df):
    st.title("📊 Rapport")
    if df.empty:
        st.info("Aucune donnée.")
        return
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    c1,c2,c3 = st.columns(3)
    annee = c1.selectbox("Année", annees, index=len(annees)-1) if annees else None
    pf_opt = ["Toutes"] + sorted(df["plateforme"].dropna().unique().tolist())
    pf = c2.selectbox("Plateforme", pf_opt)
    mois_opt = ["Tous"] + [f"{i:02d}" for i in range(1,13)]
    mois = c3.selectbox("Mois", mois_opt)

    data = df.copy()
    if annee: data = data[data["AAAA"] == int(annee)]
    if pf != "Toutes": data = data[data["plateforme"] == pf]
    if mois != "Tous": data = data[data["MM"] == int(mois)]

    if data.empty:
        st.info("Aucune donnée pour ces filtres.")
        return

    detail = data.copy()
    for c in ["date_arrivee","date_depart"]:
        detail[c] = detail[c].apply(fmt_date)
    by = [c for c in ["appartement","date_arrivee","nom_client"] if c in detail.columns]
    if by: detail = detail.sort_values(by=by, na_position="last").reset_index(drop=True)

    cols = ["appartement","plateforme","nom_client","telephone",
            "date_arrivee","date_depart","nuitees",
            "montant_net","commissions","frais_cb","montant_brut",
            "menage","taxes_sejour","base","%","sms"]
    cols = [c for c in cols if c in detail.columns]
    st.dataframe(detail[cols], use_container_width=True)

    # Totaux
    st.markdown(totaux_html(data), unsafe_allow_html=True)

def vue_clients(df):
    st.title("👥 Clients")
    if df.empty:
        st.info("Aucune donnée.")
        return
    c1,c2 = st.columns(2)
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    annee = c1.selectbox("Année", annees, index=len(annees)-1) if annees else None
    mois  = c2.selectbox("Mois", ["Tous"]+[f"{i:02d}" for i in range(1,13)])

    data = df.copy()
    if annee: data = data[data["AAAA"] == int(annee)]
    if mois != "Tous": data = data[data["MM"] == int(mois)]
    if data.empty:
        st.info("Aucune donnée pour cette période.")
        return

    show = data.copy()
    for c in ["date_arrivee","date_depart"]:
        show[c] = show[c].apply(fmt_date)
    cols = ["appartement","nom_client","plateforme","telephone","date_arrivee","date_depart","nuitees",
            "montant_brut","montant_net","commissions","frais_cb","menage","taxes_sejour","base","%","sms"]
    cols = [c for c in cols if c in show.columns]
    st.dataframe(show[cols], use_container_width=True)

    st.download_button(
        "📥 Télécharger (CSV)",
        data=show[cols].to_csv(index=False).encode("utf-8"),
        file_name="clients_multi.csv",
        mime="text/csv"
    )

# ============================ SMS (MANUEL) ============================

def log_sms(action: str, row: pd.Series, message: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rec = {
        "timestamp": now,
        "action": action,
        "appartement": row.get("appartement",""),
        "plateforme": row.get("plateforme",""),
        "nom_client": row.get("nom_client",""),
        "telephone": normalize_tel(row.get("telephone")),
        "date_arrivee": fmt_date(row.get("date_arrivee")),
        "date_depart": fmt_date(row.get("date_depart")),
        "message": message.replace("\n"," ").strip()[:1000]
    }
    try:
        if os.path.exists(SMS_LOG):
            old = pd.read_csv(SMS_LOG)
            out = pd.concat([old, pd.DataFrame([rec])], ignore_index=True)
        else:
            out = pd.DataFrame([rec])
        out.to_csv(SMS_LOG, index=False)
    except Exception as e:
        st.error(f"Impossible d'écrire le journal SMS : {e}")

def sms_msg_arrivee(row: pd.Series) -> str:
    d1s, d2s = fmt_date(row.get("date_arrivee")), fmt_date(row.get("date_depart"))
    nuits = int(row.get("nuitees") or 0)
    return (
        "VILLA TOBIAS\n"
        f"Plateforme : {row.get('plateforme','')}\n"
        f"Date d'arrivee : {d1s}  Date depart : {d2s}  Nombre de nuitees : {nuits}\n\n"
        f"Bonjour {row.get('nom_client','')}\n"
        f"Telephone : {normalize_tel(row.get('telephone'))}\n\n"
        "Bienvenue chez nous !\n\n "
        "Nous sommes ravis de vous accueillir bientot. Pour organiser au mieux votre reception, pourriez-vous nous indiquer "
        "a quelle heure vous pensez arriver.\n\n "
        "Sachez egalement qu'une place de parking est a votre disposition dans l'immeuble, en cas de besoin.\n\n "
        "Nous vous souhaitons un excellent voyage et nous nous rejouissons de vous rencontrer.\n\n "
        "Annick & Charley"
    )

def sms_msg_depart(row: pd.Series) -> str:
    return (
        f"Bonjour {row.get('nom_client','')},\n\n"
        "Un grand merci d’avoir choisi notre appartement pour votre séjour ! "
        "Nous espérons que vous avez passé un moment aussi agréable que celui que nous avons eu à vous accueillir.\n\n"
        "Si l’envie vous prend de revenir explorer encore un peu notre ville (ou simplement retrouver le confort de notre petit cocon), "
        "sachez que notre porte vous sera toujours grande ouverte.\n\n"
        "Au plaisir de vous accueillir à nouveau,\n"
        "Annick & Charley"
    )

def vue_sms(df):
    st.title("✉️ SMS (manuel)")
    if df.empty:
        st.info("Aucune donnée.")
        return

    today = date.today()
    demain = today + timedelta(days=1)
    hier = today - timedelta(days=1)

    c1,c2 = st.columns(2)

    # Arrivées demain
    with c1:
        st.subheader("📆 Arrivées demain")
        subset = df[df["date_arrivee"] == demain].copy()
        if subset.empty:
            st.info("Aucune arrivée demain.")
        else:
            for idx, r in subset.reset_index(drop=True).iterrows():
                body = sms_msg_arrivee(r)
                tel = normalize_tel(r.get("telephone"))
                tel_link = f"tel:{tel}" if tel else ""
                sms_link = f"sms:{tel}?&body={quote(body)}" if tel else ""

                st.markdown(f"**{r.get('nom_client','')}** — {r.get('plateforme','')} — {r.get('appartement','')}")
                st.markdown(f"{fmt_date(r.get('date_arrivee'))} → {fmt_date(r.get('date_depart'))} • {r.get('nuitees','')} nuitées")
                st.code(body)

                cc1, cc2, cc3 = st.columns([1,1,2])
                send_call = cc1.checkbox("📞 Appeler", key=f"arr_call_{idx}", value=False)
                send_sms  = cc2.checkbox("📩 SMS", key=f"arr_sms_{idx}", value=True)
                with cc3:
                    if send_call and tel_link:
                        st.link_button(f"Appeler {tel}", tel_link)
                    if send_sms and sms_link:
                        if st.link_button("Envoyer SMS", sms_link, key=f"arr_btn_{idx}"):
                            # Marquer comme envoyé + log si l’utilisateur a cliqué
                            r_idx = df.index[subset.index[idx]]
                            df.at[r_idx, "sms"] = "🟢 Envoyé"
                            sauvegarder_donnees(df)
                            log_sms("arrivee", r, body)
                st.divider()

    # Départs (J-1) pour relance
    with c2:
        st.subheader("🕒 Relance +24h après départ")
        subset = df[df["date_depart"] == hier].copy()
        if subset.empty:
            st.info("Aucun départ hier.")
        else:
            for idx, r in subset.reset_index(drop=True).iterrows():
                body = sms_msg_depart(r)
                tel = normalize_tel(r.get("telephone"))
                tel_link = f"tel:{tel}" if tel else ""
                sms_link = f"sms:{tel}?&body={quote(body)}" if tel else ""

                st.markdown(f"**{r.get('nom_client','')}** — {r.get('plateforme','')} — {r.get('appartement','')}")
                st.code(body)

                cc1, cc2, cc3 = st.columns([1,1,2])
                send_call = cc1.checkbox("📞 Appeler", key=f"dep_call_{idx}", value=False)
                send_sms  = cc2.checkbox("📩 SMS", key=f"dep_sms_{idx}", value=True)
                with cc3:
                    if send_call and tel_link:
                        st.link_button(f"Appeler {tel}", tel_link)
                    if send_sms and sms_link:
                        if st.link_button("Envoyer SMS", sms_link, key=f"dep_btn_{idx}"):
                            r_idx = df.index[subset.index[idx]]
                            df.at[r_idx, "sms"] = "🟢 Envoyé"
                            sauvegarder_donnees(df)
                            log_sms("depart+24h", r, body)
                st.divider()

    st.subheader("🗒️ Journal des SMS")
    if os.path.exists(SMS_LOG):
        log_df = pd.read_csv(SMS_LOG)
        st.dataframe(log_df, use_container_width=True)
        st.download_button(
            "⬇️ Télécharger journal (CSV)",
            data=log_df.to_csv(index=False).encode("utf-8"),
            file_name="sms_log_multi.csv",
            mime="text/csv"
        )
    else:
        st.info("Aucun SMS journalisé pour le moment.")

# ============================ APP ============================

def render_cache_sidebar():
    st.sidebar.markdown("---")
    st.sidebar.markdown("## 🧰 Maintenance")
    if st.sidebar.button("♻️ Vider le cache et relancer"):
        try: st.cache_data.clear()
        except: pass
        try: st.cache_resource.clear()
        except: pass
        st.rerun()

def main():
    st.set_page_config(page_title="🏘️ Réservations Multi", layout="wide")

    colors = load_platforms()

    # Fichier (sauvegarde/restauration)
    df_tmp = charger_donnees()
    bouton_fichier_sidebar(df_tmp)

    # Gestion plateformes (couleurs)
    ui_platform_manager(colors)

    st.sidebar.markdown("### 🧭 Navigation")
    onglet = st.sidebar.radio(
        "Aller à",
        ["📋 Réservations","➕ Ajouter","✏️ Modifier / Supprimer","📅 Calendrier","📊 Rapport","👥 Clients","✉️ SMS"],
    )

    render_cache_sidebar()

    df = charger_donnees()

    if onglet == "📋 Réservations":
        vue_reservations(df, colors)
    elif onglet == "➕ Ajouter":
        vue_ajouter(df, colors)
    elif onglet == "✏️ Modifier / Supprimer":
        vue_modifier(df, colors)
    elif onglet == "📅 Calendrier":
        vue_calendrier(df, colors)
    elif onglet == "📊 Rapport":
        vue_rapport(df)
    elif onglet == "👥 Clients":
        vue_clients(df)
    elif onglet == "✉️ SMS":
        vue_sms(df)

if __name__ == "__main__":
    main()