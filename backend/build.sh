#!/usr/bin/env bash
# Render build step. Migrates the control plane only — tenant databases are
# migrated when their org is created, and on demand thereafter.
set -o errexit

pip install -r backend/requirements.txt
python backend/manage.py collectstatic --no-input
python backend/manage.py migrate --database=default --no-input
