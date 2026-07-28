#!/bin/bash
apt-get update
apt-get install -y ffmpeg
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
python app.py
