#!/bin/bash
cd /home/ubuntu/apps/noxa-monitor
source .venv/bin/activate
python3 monitor.py 2>&1
