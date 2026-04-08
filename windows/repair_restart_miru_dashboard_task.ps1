# RETIRED: The RestartMiruDashboard scheduled task is no longer used.
# PM now runs as the normal user (Session 2), not as SYSTEM (Session 0).
# Workers restart PM directly without elevation.
#
# Canonical PM restart command:
#   powershell -ExecutionPolicy Bypass -File windows\restart_pm.ps1
#
# Canonical Miru AI restart command:
#   powershell -ExecutionPolicy Bypass -File windows\restart_miru_ai.ps1
#
# If the RestartMiruDashboard task still exists in Task Scheduler, it is harmless
# but unused. It can be removed from an elevated shell:
#   Unregister-ScheduledTask -TaskName "RestartMiruDashboard" -Confirm:$false
#
# Do not recreate SYSTEM-owned restart tasks.
throw "This script is retired. PM restart no longer uses scheduled tasks."
