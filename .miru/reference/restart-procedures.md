# Reference — Restart Procedures

```
Reference: restart-procedures
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: restarting a service.
Last reviewed: 2026-05-08
```

## Restart Rules

- PM (18080): `powershell -ExecutionPolicy Bypass -File windows\restart_pm.ps1`
- Miru AI (18765): `powershell -ExecutionPolicy Bypass -File windows\restart_miru_ai.ps1`
- Never use nssm restart directly
- Never create alternate restart scripts
