#!/usr/bin/env bash
# Render build step. Migrates the control plane, then every org's tenant
# database; an unreachable org is reported but does not fail the build.
set -o errexit

pip install -r backend/requirements.txt
python backend/manage.py collectstatic --no-input
python backend/manage.py migrate --database=default --no-input
python backend/manage.py migrate_tenants
