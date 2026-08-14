#!/bin/sh
set -eu
python -m app.seed
printf '%s\n' 'Seed completed.'
