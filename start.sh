#!/bin/sh
cd whatsapp_bridge && node index.js &
cd ..
python -u proxy.py &
python -u bot.py
