# 📡 Trading Radar

Intradags-radar för svenska aktier (Streamlit). Visar KÖP/SÄLJ/AVVAKTA utifrån VWAP, EMA, ADX, RSI, ATR och relativ volym, samt färdiga ordernivåer för Avanza/Nordnet.

- Huvudfil: `app.py`
- Strategiparametrar och standardlista: `config.py`
- Pushnotiser: lägg in `NTFY_TOPIC = "ditt-hemliga-namn"` under appens *Secrets* på Streamlit Community Cloud.

Data från Yahoo Finance är ca 15 min fördröjd. Analysverktyg – inte investeringsrådgivning.
