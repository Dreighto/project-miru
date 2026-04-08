# RETIRED: This script used a SYSTEM-owned scheduled task to restart Miru AI.
# That approach created Session 0 ownership which prevented non-elevated workers from managing Miru AI.
#
# Canonical Miru AI restart command (runs as normal user, no elevation needed):
#   powershell -ExecutionPolicy Bypass -File windows\restart_miru_ai.ps1
#
# Do not restore the scheduled-task path.
throw "This script is retired. Use: powershell -ExecutionPolicy Bypass -File windows\restart_miru_ai.ps1"
