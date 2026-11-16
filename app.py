"""Trading Radar – Streamlit-gränssnitt (molnversion för Streamlit Community Cloud)."""
from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import config as C
from analyzer import AnalysisResult, trade_plan
from engine import ScannerEngine
from notifier import send_ntfy

st.set_page_config(page_title="Trading Radar", page_icon="📡", layout="wide")

WEEKDAYS = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"]
STATUS_ORDER = {C.STATUS_BUY: 0, C.STATUS_SELL: 1, C.STATUS_WAIT: 2, C.STATUS_LOADING: 3, C.STATUS_ERROR: 4}
STATUS_TINT = {
    C.STATUS_BUY: "background-color: rgba(22,163,74,0.18); font-weight: 600",
    C.STATUS_SELL: "background-color: rgba(220,38,38,0.18); font-weight: 600",
    C.STATUS_WAIT: "",
}

st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem;}
      div[data-testid="stMetricValue"] {font-size: 1.6rem;}
      .tr-big {font-size: 1.05rem; line-height: 1.7;}
      .tr-muted {opacity: .7; font-size: .85rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Hjälpfunktioner
# ---------------------------------------------------------------------------
def nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def sek(x: float, dec: int = 2, sign: bool = False) -> str:
    if nan(x):
        return "–"
    s = f"{x:+,.{dec}f}" if sign else f"{x:,.{dec}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−")


def when(ts: pd.Timestamp | None) -> str:
    if ts is None:
        return "–"
    today = pd.Timestamp.now(tz=C.TIMEZONE).normalize()
    return f"{ts:%H:%M}" if ts.normalize() == today else f"{WEEKDAYS[ts.weekday()]} {ts:%H:%M}"


def stretch(fn, *args, **kwargs):
    """Fullbredd oavsett Streamlit-version."""
    try:
        return fn(*args, width="stretch", **kwargs)
    except Exception:
        return fn(*args, use_container_width=True, **kwargs)


@st.cache_resource(show_spinner=False)
def get_engine() -> ScannerEngine:
    C.LOG_DIR.mkdir(parents=True, exist_ok=True)
    engine = ScannerEngine()
    engine.start()
    return engine


def ntfy_topic() -> str:
    try:
        return str(st.secrets.get("NTFY_TOPIC", "")).strip()
    except Exception:
        return ""


def beep() -> None:
    """Kort ljudsignal i webbläsaren (fungerar när du har klickat någonstans på sidan)."""
    js = """<script>
    try { const a = new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.18].forEach(t => { const o = a.createOscillator(), g = a.createGain();
        o.frequency.value = 880; o.connect(g); g.connect(a.destination);
        g.gain.setValueAtTime(0.15, a.currentTime + t); o.start(a.currentTime + t);
        o.stop(a.currentTime + t + 0.12); }); } catch (e) {}
    </script>"""
    try:
        if hasattr(st, "iframe"):
            st.iframe(js, height=1)
        else:
            import streamlit.components.v1 as components
            components.html(js, height=0)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
engine = get_engine()
engine.notifier.topic = ntfy_topic()
st_autorefresh(interval=C.REFRESH_SECONDS * 1000, key="auto_refresh")

# Larm som rutor + ljud i appen (bara nya larm sedan fliken öppnades)
if "seen_event" not in st.session_state:
    st.session_state.seen_event = engine.notifier.last_event_id()
new_events = engine.notifier.events_after(st.session_state.seen_event)
for ev in new_events:
    st.toast(f"**{ev['status']} {ev['ticker']}** @ {ev['price']:.2f} SEK · {ev['time']:%H:%M}",
             icon="🟢" if ev["status"] == C.STATUS_BUY else "🔴")
if new_events:
    st.session_state.seen_event = new_events[-1]["id"]
    beep()

if "order_sek" not in st.session_state:
    st.session_state.order_sek = int(C.load_settings().get("order_sek", C.DEFAULT_ORDER_SEK))

# ---------------------------------------------------------------------------
# Sidopanel
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 📡 Trading Radar")

    st.number_input(
        "Belopp per affär (SEK)", min_value=C.ORDER_STEP_SEK, step=C.ORDER_STEP_SEK,
        key="order_sek", on_change=lambda: C.save_settings(order_sek=int(st.session_state.order_sek)),
        help="Hur mycket du vill lägga i varje affär. Antal aktier räknas ut automatiskt.",
    )
    order_sek = float(st.session_state.order_sek)

    watchlist = C.load_watchlist()
    kept = st.multiselect(
        "Bevakade aktier", options=watchlist, default=watchlist, key=f"wl_{'|'.join(watchlist)}",
        placeholder="Inga aktier – lägg till nedan", help="Klicka på ✕ för att sluta bevaka en aktie.",
    )
    if kept != watchlist:
        C.save_watchlist(kept)
        engine.refresh_soon()
        st.rerun()

    with st.form("add_ticker", clear_on_submit=True, border=False):
        new = st.text_input("Lägg till aktie", placeholder="t.ex. HM-B eller ERIC-B.ST")
        if st.form_submit_button("➕ Lägg till") and new.strip():
            t = C.normalize_ticker(new)
            if t in watchlist:
                st.info(f"{t} bevakas redan.")
            else:
                with st.spinner(f"Kontrollerar {t} …"):
                    test = engine.scan_ticker(t)
                if test.error:
                    st.error(f"Hittade ingen data för **{t}**. Svenska aktier har ändelsen .ST "
                             f"(samma kortnamn som på Avanza, t.ex. HM-B.ST).")
                else:
                    C.save_watchlist(watchlist + [t])
                    engine.refresh_soon()
                    st.rerun()

    st.divider()
    st.info(
        f"**Testläge:** data från {engine.provider.name} är {engine.provider.delay_note}. "
        "Stäm alltid av mot realtidsorderboken i **Avanza/Nordnet** innan du lägger en order.",
        icon="ℹ️",
    )

    topic = engine.notifier.topic
    if topic:
        st.markdown(f"🔔 Notiser via ntfy: prenumerera på **{topic}** på "
                    f"[ntfy.sh/app](https://ntfy.sh/app) eller i ntfy-appen i mobilen.")
        if st.button("🔔 Skicka testnotis"):
            ok = send_ntfy(topic, "Trading Radar – testnotis", "Så här ser ett larm ut.", tags=["satellite"])
            st.toast("Testnotis skickad!" if ok else "Kunde inte nå ntfy just nu.")
    else:
        st.caption("🔕 Pushnotiser är inte aktiverade (NTFY_TOPIC saknas i appens Secrets). "
                   "Larm visas ändå som rutor här i appen.")
    st.caption("I molnet återställs bevakningslistan till standardlistan om appen startas om. "
               "Ändra DEFAULT_TICKERS i config.py för att spara din lista permanent.")

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
if not engine.wait_for_first_scan(timeout=0):
    with st.spinner("Hämtar marknadsdata för första gången …"):
        engine.wait_for_first_scan(timeout=90)

results, last_scan = engine.snapshot()
watchlist = C.load_watchlist()

now = pd.Timestamp.now(tz=C.TIMEZONE)
in_window_now = C.SIGNAL_WINDOW_START <= now.time() <= C.SIGNAL_WINDOW_END and now.weekday() < 5
win_txt = f"{C.SIGNAL_WINDOW_START:%H:%M}–{C.SIGNAL_WINDOW_END:%H:%M}"

st.title("📡 Trading Radar")
st.caption(
    f"Signalfönster {win_txt} · just nu: {'🟢 aktivt' if in_window_now else '⚪ stängt – status visas som AVVAKTA'}"
    f" · senast uppdaterad {last_scan:%H:%M:%S}" if last_scan else "Väntar på första skanningen …"
)

rows = []
for t in watchlist:
    r = results.get(t)
    if r is None:
        rows.append({"Status": C.STATUS_LOADING, "Aktie": t})
        continue
    rows.append({
        "Status": r.status, "Aktie": t,
        "Senaste pris (SEK)": None if nan(r.price) else r.price,
        "Avstånd till VWAP (%)": None if nan(r.vwap_dist_pct) else r.vwap_dist_pct,
        "RVOL": None if nan(r.rvol) else r.rvol,
        "ADX": None if nan(r.adx) else r.adx,
        "RSI": None if nan(r.rsi) else r.rsi,
        "Tid för senaste signal": (f"{r.last_signal_status.split()[0]} {when(r.last_signal_time)}"
                                   if r.last_signal_time is not None else "–"),
    })
table = pd.DataFrame(rows, columns=["Status", "Aktie", "Senaste pris (SEK)", "Avstånd till VWAP (%)",
                                    "RVOL", "ADX", "RSI", "Tid för senaste signal"])
table = table.sort_values("Status", key=lambda s: s.map(STATUS_ORDER).fillna(9), kind="stable")

# ---------------------------------------------------------------------------
# Översikt
# ---------------------------------------------------------------------------
st.subheader("Bevakning")
only_active = st.checkbox("Visa endast aktier med aktiv signal (Köp/Sälj)")
shown = table[table["Status"].isin([C.STATUS_BUY, C.STATUS_SELL])] if only_active else table

if shown.empty:
    st.info("Inga aktiva signaler just nu. Radarn fortsätter bevaka och skickar en notis när något händer."
            if only_active else "Bevakningslistan är tom – lägg till aktier i sidopanelen.")
else:
    styler = shown.style
    styler = (styler.map if hasattr(styler, "map") else styler.applymap)(
        lambda v: STATUS_TINT.get(v, ""), subset=["Status"])
    stretch(
        st.dataframe, styler, hide_index=True,
        column_config={
            "Senaste pris (SEK)": st.column_config.NumberColumn(format="%.2f"),
            "Avstånd till VWAP (%)": st.column_config.NumberColumn(
                format="%+.2f %%", help="Hur långt priset ligger över (+) eller under (−) dagens VWAP."),
            "RVOL": st.column_config.NumberColumn(format="%.2f", help="Relativ volym. Över 1,5 = ovanligt mycket handel."),
            "ADX": st.column_config.NumberColumn(format="%.1f", help="Trendstyrka. Över 22 = tydlig trend."),
            "RSI": st.column_config.NumberColumn(format="%.1f", help="Momentum 0–100. 50–70 = sunt köptryck."),
        },
    )

# ---------------------------------------------------------------------------
# Detaljvy
# ---------------------------------------------------------------------------
st.divider()
options = list(shown["Aktie"]) or list(table["Aktie"])
if not options:
    st.stop()
if st.session_state.get("selected") not in options:
    st.session_state.selected = options[0]
selected = st.selectbox("🔍 Detaljvy för aktie", options, key="selected")
r: AnalysisResult | None = results.get(selected)

if r is None:
    st.info("Data hämtas – vänta en stund.")
    st.stop()
if r.error:
    st.error(f"**{selected}:** {r.error}")
    st.stop()

banner = {C.STATUS_BUY: st.success, C.STATUS_SELL: st.error}.get(r.status, st.warning)
banner(f"**{r.status}** – {r.status_reason}")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Senaste pris", f"{sek(r.price)} SEK")
m2.metric("VWAP", f"{sek(r.vwap)} SEK",
          None if nan(r.vwap_dist_pct) else f"{r.vwap_dist_pct:+.2f} % pris vs VWAP".replace(".", ","))
m3.metric("ATR (14, 5 min)", f"{sek(r.atr)} SEK", help="Genomsnittlig rörelse per 5-minutersstapel. Styr stop loss och målkurs.")
m4.metric("Senaste stapel", when(r.bar_time))

# --- Order-kort -----------------------------------------------------------
if r.status == C.STATUS_SELL:
    direction = "short"
elif r.status == C.STATUS_BUY:
    direction = "long"
else:
    direction = "short" if r.last_signal_status == C.STATUS_SELL else "long"
plan = trade_plan(r.price, r.atr, direction, order_sek)

st.markdown("### 🧾 Gör så här i Avanza / Nordnet")
if not r.is_signal:
    st.caption("⚠️ Ingen aktiv signal just nu – siffrorna nedan är bara ett **räkneexempel**. Lägg ingen order.")
elif direction == "short":
    st.caption("SÄLJ betyder: sälj aktier du äger, eller gå kort (blankning/bear-certifikat). "
               "Nivåerna nedan gäller en kort position.")

if plan is None:
    st.warning("För lite data för att räkna ut nivåer (ATR saknas).")
else:
    long = plan.direction == "long"
    k1, k2, k3 = st.columns(3)
    with k1.container(border=True):
        st.markdown(f"#### 1️⃣ {'Köp' if long else 'Sälj'} / Ingång")
        st.metric("Antal aktier", f"{plan.shares} st")
        st.markdown(f"<div class='tr-big'>Kurs: <b>{sek(plan.entry)} SEK</b><br>"
                    f"Orderbelopp: <b>{sek(plan.order_value)} SEK</b></div>", unsafe_allow_html=True)
        st.caption(f"Lägg en limit-{'köp' if long else 'sälj'}order nära aktuell kurs.")
    with k2.container(border=True):
        st.markdown("#### 2️⃣ Stop Loss (Skydd)")
        st.metric("Triggerkurs", f"{sek(plan.stop_trigger)} SEK")
        st.markdown(f"<div class='tr-big'>Orderkurs: <b>{sek(plan.stop_order)} SEK</b> "
                    f"<span class='tr-muted'>(0,4 % marginal)</span><br>"
                    f"Max risk: <b>{sek(-plan.max_loss_sek, 0)} SEK</b> ({sek(-plan.max_loss_pct)} %)</div>",
                    unsafe_allow_html=True)
        st.caption(f"Stop loss-order: när kursen {'faller till' if long else 'stiger till'} triggern läggs "
                   f"en {'sälj' if long else 'köp'}order på orderkursen.")
    with k3.container(border=True):
        st.markdown("#### 3️⃣ Take Profit (Målkurs)")
        st.metric("Målkurs", f"{sek(plan.take_profit)} SEK")
        st.markdown(f"<div class='tr-big'>Förväntad vinst: <b>{sek(plan.expected_profit_sek, 0, sign=True)} SEK</b> "
                    f"({sek(plan.expected_profit_pct, sign=True)} %)<br>"
                    f"Risk/Reward: <b>1:{plan.risk_reward:.0f}</b></div>", unsafe_allow_html=True)
        st.caption(f"Lägg en limit-{'sälj' if long else 'köp'}order på målkursen.")

# --- Graf -----------------------------------------------------------------
f = r.frame
sessions = f.index.normalize()
start_day = sessions[-1]
if r.last_signal_time is not None:
    start_day = min(start_day, r.last_signal_time.normalize())
view = f[sessions >= start_day]
x = view.index.tz_localize(None)

fig = go.Figure()
fig.add_trace(go.Scatter(x=x, y=view["Close"], name="Pris", mode="lines",
                         line=dict(color="#3b82f6", width=2.2)))
fig.add_trace(go.Scatter(x=x, y=view["vwap"], name="VWAP", mode="lines",
                         line=dict(color="#f59e0b", width=1.8, dash="dash")))
if r.last_signal_time is not None:
    is_buy = r.last_signal_status == C.STATUS_BUY
    color = "#16a34a" if is_buy else "#dc2626"
    fig.add_trace(go.Scatter(
        x=[r.last_signal_time.tz_localize(None)], y=[r.last_signal_price], mode="markers+text",
        name="Senaste signal", text=[f"{'KÖP' if is_buy else 'SÄLJ'} {r.last_signal_time:%H:%M}"],
        textposition="top center" if is_buy else "bottom center", textfont=dict(color=color, size=13),
        marker=dict(symbol="triangle-up" if is_buy else "triangle-down", size=18, color=color,
                    line=dict(color="white", width=1.5)),
    ))
fig.update_layout(
    height=420, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    yaxis_title="SEK",
)
fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"]), dict(bounds=[17.5, 9], pattern="hour")])
st.markdown(f"### 📈 {selected} – pris och VWAP")
stretch(st.plotly_chart, fig, config={"displayModeBar": False})

# --- Regelkontroll -----------------------------------------------------------
with st.expander("Varför den här statusen? Se regelkontrollen"):
    b, s = st.columns(2)
    for col, title, checks in ((b, "🟢 KÖP-villkor", r.buy_checks), (s, "🔴 SÄLJ-villkor", r.sell_checks)):
        col.markdown(f"**{title}**")
        for c in checks:
            col.markdown(f"{'✅' if c.ok else '❌'} {c.label} <span class='tr-muted'>({c.detail})</span>",
                         unsafe_allow_html=True)
    st.caption("En signal kräver att ALLA villkor i en kolumn är uppfyllda samtidigt.")

st.caption("Trading Radar är ett analysverktyg, inte investeringsrådgivning. Du fattar alltid själv beslutet.")
