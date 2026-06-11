#!/bin/sh
(cd whatsapp_bridge && node index.js) &
python -u proxy.py &
python -u bot.py

