# AGENTS.md — wakemeupManager

## Entorno de pruebas / dispositivos

- **Tablet Samsung (SM-T220, serial `R83W50BL1WM`)**: conectada al NucBox por **USB** para pruebas de la app Android.
- Esa tablet está **logueada a Tailscale**, así que puede alcanzar el BE directamente por la tailnet (a efectos de prueba). Ya no hace falta `adb reverse` para llegar al BE desde la tablet.

## Proceso / convenciones (del usuario, en vigor)

- Commit + push obligatorio al final de CADA story.
- Todo el código cubierto de tests: BE con pytest; app Android con JVM + Robolectric; integración del contrato API.
- Al revisar con MockEngine: asertar URL/Authorization del request (los matchers `endsWith` ciegan la validación real).
- Adaptadores con IO real (SSH/SFTP): test con doble que respete firmas async/tipos + verificación en máquina real.
- No commitear salvo petición explícita del usuario.
