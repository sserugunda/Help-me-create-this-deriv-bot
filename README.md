# Help-me-create-this-deriv-bot
Deriv Volatility Indices 1m Rise Bot

Environment (.env):

DERIV_APP_ID=xxxxx
DERIV_API_TOKEN=xxxxxx
TELEGRAM_BOT_TOKEN=xxxxx
TELEGRAM_CHAT_ID=123456789  # optional
SYMBOLS=R_10,R_25,R_50,R_75,R_100,RV_10,RV_25,RV_50,RV_75,RV_100,RDBEAR,RDBULL,R_200
STAKE_PCT=1
GRANULARITY=60
PERC_STEP=1
MIN_SAMPLES=1

Scripts:
- npm run dev
- npm run build && npm start
