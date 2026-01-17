#!/bin/bash

cd /home/admin/consoled

sudo $(which python3) -m console_monitor.console_monitor "$@"